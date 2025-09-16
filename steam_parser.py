#!/usr/bin/env python3
"""
Steam Game Parser
Parses games from games_list.txt and extracts Steam store information
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import os
import signal
import sys
from urllib.parse import quote
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SteamParser:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.output_file = 'steam_games.xlsx'
        self.existing_games = set()
        self.current_results = []
        self.interrupted = False
        
        # Define the fields to extract and their corresponding extraction methods
        self.fields_to_extract = {
            'price': self._extract_price,
            'release_date': self._extract_release_date,
            'dev': self._extract_dev,
            'metascore': self._extract_metascore,
            'reviews_count': self._extract_reviews_count,
            'reviews_tone': self._extract_reviews_tone,
            'russian_voiceover': self._extract_russian_voiceover,
            'tags': self._extract_tags,
            'pegi': self._extract_pegi,
            'played': self._extract_played_hours,
            'detail_url': lambda soup, detail_url: detail_url  # Special case for URL
        }
        
        # Load environment variables
        load_dotenv()
        self._setup_steam_cookies()
        
        # Setup signal handler for Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C interruption"""
        logger.info(f"Received signal {signum}, saving current data and exiting gracefully...")
        self.interrupted = True
        
        # Save current results if any
        if self.current_results:
            logger.info(f"Saving {len(self.current_results)} processed games before exit...")
            self.save_to_excel(self.current_results)
        
        sys.exit(0)
    
    def _setup_steam_cookies(self):
        """Set up Steam authentication cookies from environment variables"""
        sessionid = os.getenv('STEAM_SESSIONID')
        steam_login_secure = os.getenv('STEAM_LOGIN_SECURE')
        steam_parental = os.getenv('STEAM_PARENTAL')
        lastagecheckage = os.getenv('lastagecheckage')
        birthtime = os.getenv('birthtime')
        wants_mature_content = os.getenv('wants_mature_content')
        
        if sessionid and steam_login_secure:
            # Set cookies for authenticated requests
            self.session.cookies.set('sessionid', sessionid, domain='store.steampowered.com')
            self.session.cookies.set('steamLoginSecure', steam_login_secure, domain='store.steampowered.com')
            self.session.cookies.set('lastagecheckage', lastagecheckage, domain='store.steampowered.com')
            self.session.cookies.set('birthtime', birthtime, domain='store.steampowered.com')
            self.session.cookies.set('wants_mature_content', wants_mature_content, domain='store.steampowered.com')
            if steam_parental:
                self.session.cookies.set('steamparental', steam_parental, domain='store.steampowered.com')
            
            # Set optional cookies if available
            steam_language = os.getenv('STEAM_LANGUAGE')
            steam_timezone_offset = os.getenv('STEAM_TIMEZONE_OFFSET')
            
            if steam_language:
                self.session.cookies.set('Steam_Language', steam_language, domain='store.steampowered.com')
            if steam_timezone_offset:
                self.session.cookies.set('timezoneOffset', steam_timezone_offset, domain='store.steampowered.com')
            
            logger.info("Steam authentication cookies loaded from environment variables")
            
            # Test authentication by making a simple request to profile page
            # if not self._test_authentication():
            #     # If authentication test fails, clear invalid cookies and run without auth
            #     logger.warning("Clearing invalid authentication cookies")
            #     self.session.cookies.clear(domain='store.steampowered.com')
            #     logger.warning("Running without authentication")
        else:
            logger.warning("STEAM_SESSIONID or STEAM_LOGIN_SECURE not found in environment variables")
            logger.warning("Running without authentication")
            
    def _test_authentication(self):
        """Test if authentication is working by accessing profile page"""
        try:
            test_url = "https://store.steampowered.com/account/"
            response = self.session.get(test_url, timeout=10)
            
            if response.status_code == 200:
                logger.info("Authentication test successful - cookies are valid")
                return True
            else:
                logger.warning(f"Authentication test failed with status {response.status_code}")
                logger.warning("Cookies may be expired or invalid")
                return False
                
        except Exception as e:
            logger.error(f"Authentication test error: {e}")
            return False
            
    def set_steam_cookies_manual(self, cookies_dict):
        """Manually set Steam cookies (alternative to environment variables)"""
        for cookie_name, cookie_value in cookies_dict.items():
            self.session.cookies.set(cookie_name, cookie_value, domain='store.steampowered.com')
        logger.info("Steam cookies set manually")
        
    def load_existing_games(self):
        """Load existing games from Excel file to avoid duplicates"""
        if os.path.exists(self.output_file):
            try:
                df = pd.read_excel(self.output_file)
                self.existing_games = set(df['game_name'].astype(str).str.strip().values)
                logger.info(f"Loaded {len(self.existing_games)} existing games from {self.output_file}")
            except Exception as e:
                logger.warning(f"Could not read existing Excel file: {e}")
                self.existing_games = set()
    
    def read_games_list(self, filename='games_list.txt'):
        """Read game names from file"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                games = [line.strip() for line in f.readlines() if line.strip()]
            logger.info(f"Read {len(games)} games from {filename}")
            return games
        except FileNotFoundError:
            logger.error(f"File {filename} not found")
            return []
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return []
    
    def _calculate_similarity(self, search_name, match_name):
        """Calculate similarity score between search name and match name"""
        # Simple similarity calculation
        search_words = set(search_name.lower().split())
        match_words = set(match_name.lower().split())
        
        # Calculate Jaccard similarity
        intersection = search_words.intersection(match_words)
        union = search_words.union(match_words)
        
        if not union:
            return 0
            
        return len(intersection) / len(union)
    
    def search_steam_game(self, game_name):
        """Search for game on Steam and return detail URL"""
        # Use Russian language for Russian game names, English for others
        # Check if the game name contains Cyrillic characters
        has_cyrillic = any('\u0400' <= char <= '\u04FF' for char in game_name)
        language = 'russian' if has_cyrillic else 'english'

        if "GOTY" in game_name:
            game_name = game_name.replace("GOTY", "Game of the Year").strip()
        
        search_url = f"https://store.steampowered.com/search/suggest?term={quote(game_name)}&f=games&cc=KZ&realm=1&l={language}&v=31137119&use_store_query=1&use_search_spellcheck=1&search_creators_and_tags=1"
        
        try:
            response = self.session.get(search_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find game matches (exclude creators and other non-game matches)
            game_matches = soup.find_all('a', class_=lambda x: x and 'match_app' in x and 'match_creator' not in x)
            
            if not game_matches:
                logger.warning(f"No game matches found for: {game_name}")
                return None
            
            # Find the best match by comparing names
            best_match = None
            best_score = 0
            
            for match in game_matches:
                match_name_element = match.find('div', class_='match_name')
                if match_name_element:
                    match_name = match_name_element.get_text(strip=True)
                    similarity_score = self._calculate_similarity(game_name, match_name)
                    
                    # Also check if it's a soundtrack or demo (avoid these)
                    is_soundtrack = any(word in match_name.lower() for word in ['soundtrack', 'ost', 'demo'])
                    is_bad_match = similarity_score < 0.3 or is_soundtrack
                    
                    if similarity_score > best_score and not is_bad_match:
                        best_score = similarity_score
                        best_match = match
            
            if best_match and best_score > 0.3:
                detail_url = best_match.get('href')
                if detail_url and 'store.steampowered.com/app/' in detail_url:
                    logger.info(f"Found Steam URL for {game_name}: {detail_url} (similarity: {best_score:.2f})")
                    return detail_url
                else:
                    logger.warning(f"Invalid Steam URL found for {game_name}: {detail_url}")
                    return None
            else:
                logger.warning(f"No good match found for: {game_name} (best score: {best_score:.2f})")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Request error for {game_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error searching {game_name}: {e}")
            return None
    
    def parse_game_details(self, detail_url):
        """Parse game details from Steam store page, handling age verification"""
        try:
            response = self.session.get(detail_url, timeout=10)
            response.raise_for_status()
            
            # Check if this is an age verification page
            # if self._is_age_verification_page(response):
            #     logger.info(f"Age verification required for {detail_url}")
            #     if self._handle_age_verification(response):
            #         # Retry the original request after age verification
            #         response = self.session.get(detail_url, timeout=10)
            #         response.raise_for_status()
            #     else:
            #         logger.warning(f"Failed to bypass age verification for {detail_url}")
            #         return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract game details using centralized field definition
            game_data = {}
            for field_name, extractor in self.fields_to_extract.items():
                try:
                    if field_name == 'detail_url':
                        # Special handling for detail_url which doesn't need soup
                        game_data[field_name] = extractor(soup, detail_url)
                    else:
                        game_data[field_name] = extractor(soup)
                except Exception as e:
                    logger.warning(f"Error extracting {field_name}: {e}")
                    game_data[field_name] = f"Error extracting {field_name}"
            
            return game_data
            
        except requests.RequestException as e:
            logger.error(f"Request error for {detail_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing {detail_url}: {e}")
            return None
    
    def _extract_price(self, soup):
        """Extract price from game page"""
        try:
            # Try multiple selectors for price
            price_selectors = [
                '.game_purchase_price',
                '.discount_final_price',
                '.price'
            ]
            
            for selector in price_selectors:
                price_element = soup.select_one(selector)
                if price_element:
                    price_text = price_element.get_text(strip=True)
                    if price_text:
                        return price_text
            
            # Check if game is free
            if soup.find(string=re.compile(r'free to play', re.IGNORECASE)):
                return "Free to Play"
                
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting price: {e}")
            return "Error extracting price"
    
    def _extract_release_date(self, soup):
        """Extract release date from game page and format to dd.mm.yyyy"""
        try:
            # Try multiple selectors for release date
            date_selectors = [
                '.release_date .date',
                '.release_date',
                '.date',
                '[itemprop="datePublished"]'
            ]
            
            for selector in date_selectors:
                date_element = soup.select_one(selector)
                if date_element:
                    date_text = date_element.get_text(strip=True)
                    if date_text:
                        # Format date from "16 Feb, 2012" to "16.02.2012"
                        return self._format_date(date_text)
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting release date: {e}")
            return "Error extracting release date"
    
    def _extract_dev(self, soup):
        try:
            # Try multiple selectors for release date
            selectors = [
                '.dev_row #developers_list',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    element_text = element.get_text(strip=True)
                    if element_text:
                        return element_text
            
            return "DEV not found"
            
        except Exception as e:
            logger.warning(f"Error extracting release date: {e}")
            return "Error extracting release date"

    def _extract_metascore(self, soup):
        try:
            field_name = "Metascore"
            selectors = [
                '#game_area_metascore .score',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(strip=True)
                    if text:
                        return text
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name

    def _extract_reviews_count(self, soup):
        try:
            field_name = "Reviews count"
            selectors = [
                'meta[itemprop="reviewCount"]',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element["content"]
                    if text:
                        return text
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name
        
    
    def _extract_reviews_tone(self, soup):
        try:
            field_name = "Reviews rating"
            selectors = [
                'meta[itemprop="ratingValue"]',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element["content"]
                    if text:
                        return text
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name
            
    def _extract_tags(self, soup):
        try:
            field_name = "Tags"
            selectors = [
                '.glance_tags.popular_tags',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    # Find all <a> tags within the element
                    tag_links = element.find_all('a')
                    if tag_links:
                        # Extract text from each <a> tag and join with commas
                        tags = [link.get_text(strip=True) for link in tag_links if link.get_text(strip=True)]
                        if tags:
                            return ', '.join(tags)
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name
            
    def _extract_played_hours(self, soup):
        try:
            field_name = "Played"
            selectors = [
                '.details_block.hours_played',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element.text
                    if text:
                        if len(text.split("/")) >=2:
                            return text.split("/")[1].replace("ч. всего", "").replace("hrs on record", "").strip()
                        else:
                            return text.replace("ч. всего", "").replace("hrs on record", "").strip()
            

            return 0
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name

            
    def _extract_pegi(self, soup):
        try:
            field_name = "Pegi"
            selectors = [
                '.game_rating_icon img',
            ]
            
            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    text = element["alt"]
                    if text:
                        return text
            
            return "not found"
            
        except Exception as e:
            logger.warning(f"Error extracting {field_name}: {e}")
            return "Error extracting " + field_name

    def _extract_russian_voiceover(self, soup):
        """Extract Russian voiceover availability from language options table"""
        try:
            # Find the language options table
            language_table = soup.find('table', class_='game_language_options')
            if not language_table:
                return "Не найдено"  # Table not found
            
            # Find all rows in the table body
            rows = language_table.find_all('tr')
            if len(rows) < 2:  # Need at least header row + one data row
                return "Не найдено"
            
            # Find the Russian language row
            russian_row = None
            for row in rows[1:]:  # Skip header row
                language_cell = row.find('td', class_='ellipsis')
                if language_cell and 'русский' in language_cell.get_text(strip=True).lower():
                    russian_row = row
                    break
            
            if not russian_row:
                return "Нет"  # Russian language not found in table
            
            # Find all check columns in the row
            check_columns = russian_row.find_all('td', class_='checkcol')
            if len(check_columns) < 3:  # Should have Interface, Voiceover, Subtitles columns
                return "Не найдено"
            
            # The second column (index 1) is the voiceover column
            voiceover_column = check_columns[1]
            checkmark = voiceover_column.find('span')
            
            # Check if there's a checkmark (✔) in the voiceover column
            if checkmark and '✔' in checkmark.get_text():
                return "Да"  # Russian voiceover available
            else:
                return "Нет"  # Russian voiceover not available
            
        except Exception as e:
            logger.warning(f"Error extracting Russian voiceover: {e}")
            return "Ошибка извлечения"
    

    def _format_date(self, date_string):
        """Format date from various formats to 'dd.mm.yyyy'"""
        try:
            # Month mapping (English and Russian abbreviations)
            month_map = {
                'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
                'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
                'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
                'янв': '01', 'фев': '02', 'мар': '03', 'апр': '04',
                'мая': '05', 'июн': '06', 'июл': '07', 'ав': '08',
                'сен': '09', 'окт': '10', 'ноя': '11', 'дек': '12'
            }
            
            # Clean the date string - remove commas, periods, and "г." (year abbreviation)
            date_string = date_string.replace(',', '').replace('.', '').replace('г', '').strip()
            
            # Split into parts
            parts = date_string.split()
            
            if len(parts) == 3:
                # Format like "16 Feb 2012" or "18 сен 2018"
                day, month, year = parts
            elif len(parts) == 2:
                # Format like "Feb 2012" - assume day is 1st
                month, year = parts
                day = '01'
            else:
                return date_string  # Return original if format not recognized
            
            # Remove any remaining punctuation from month and convert to lowercase for Russian months
            month_clean = month.lower().rstrip('.').rstrip()
            
            # Check if month is valid abbreviation (try both original and cleaned version)
            if month_clean in month_map:
                month_num = month_map[month_clean]
            elif month in month_map:
                month_num = month_map[month]
            else:
                return date_string  # Return original if month not recognized
            
            # Validate day and year (remove any non-digit characters)
            day_clean = ''.join(filter(str.isdigit, day))
            year_clean = ''.join(filter(str.isdigit, year))
            
            if not day_clean or not year_clean:
                return date_string
                
            # Format as dd.mm.yyyy
            day_formatted = day_clean.zfill(2)
            return f"{day_formatted}.{month_num}.{year_clean}"
            
        except Exception:
            # Return original string if formatting fails
            return date_string
    
    def _is_age_verification_page(self, response):
        """Check if the response contains an age verification page"""    
        try:
            soup = BeautifulSoup(response.text, 'html.parser')

            logger.debug(f"raw html: {response.text}")
            
            # Look for multiple age verification indicators
            age_indicators = [
                # Russian age verification text
                soup.find(string=re.compile(r'укажите дату своего рождения', re.IGNORECASE)),
                
                # English age verification text
                soup.find(string=re.compile(r'age.*check', re.IGNORECASE)),
                
                # Age gate elements
                soup.find('div.agegate_birthday_desc')
            ]
            
            # Check if any indicator is found
            return any(indicator is not None for indicator in age_indicators)
        except Exception as e:
            logger.exception(f"Error checking age verification page: {e}")
            return False
    
    def _handle_age_verification(self, response):
        """Handle Steam age verification by submitting the age check"""
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the age verification div (new Steam format)
            age_div = soup.find('div', class_='agegate_birthday_selector')
            if not age_div:
                logger.warning("No age verification div found")
                return False
            
            # Extract sessionid from cookies
            sessionid = self.session.cookies.get('sessionid')
            if not sessionid:
                logger.warning("Could not find sessionid for age verification")
                return False
            
            # Prepare age verification data (using a valid date of birth - 16 August 1995)
            form_data = {
                'sessionid': sessionid,
                'ageDay': '16',
                'ageMonth': 'August',
                'ageYear': '1995'
            }
            
            # Get the app ID from the URL to construct the age check URL
            app_id_match = re.search(r'app/(\d+)', response.url)
            if app_id_match:
                app_id = app_id_match.group(1)
                action_url = f"https://store.steampowered.com/agecheckset/{app_id}/"
            else:
                # Fallback to generic age check URL
                action_url = "https://store.steampowered.com/agecheckset/"
            
            # Submit the age verification
            logger.info(f"Submitting age verification to {action_url}")
            age_response = self.session.post(action_url, data=form_data, timeout=10)
            
            # Check if age verification was successful
            if age_response.status_code == 200:
                # Check if we're still on an age verification page
                if not self._is_age_verification_page(age_response):
                    logger.info(f"Age verification successful: {age_response.text}")
                    return True
                else:
                    logger.warning("Age verification failed - still on age verification page")
                    return False
            else:
                logger.warning(f"Age verification failed with status {age_response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error handling age verification: {e}")
            return False
    
    def process_games(self, games, delay=1):
        """Process list of games and collect data"""
        results = []
        
        for i, game_name in enumerate(games):
            # Check for interruption
            if self.interrupted:
                logger.info("Processing interrupted by user")
                break
                
            if game_name in self.existing_games:
                logger.info(f"Skipping existing game: {game_name}")
                continue
                
            logger.info(f"Processing {i+1}/{len(games)}: {game_name}")
            
            # Search for game
            detail_url = self.search_steam_game(game_name)
            if not detail_url:
                # Create result with default values for all fields when search fails
                result = {'game_name': game_name, 'status': 'Search failed'}
                for field_name in self.fields_to_extract.keys():
                    result[field_name] = 'Not found' if field_name == 'detail_url' else 'nf'
                results.append(result)
                self.current_results.append(result)
                continue
            
            # Parse game details
            game_data = self.parse_game_details(detail_url)
            if game_data:
                # Create result with all extracted fields plus game_name and status
                result = {'game_name': game_name, 'status': 'Success'}
                for field_name in self.fields_to_extract.keys():
                    result[field_name] = game_data.get(field_name, 'Not found')
                results.append(result)
                self.current_results.append(result)
            else:
                # Create result with parse failure values
                result = {'game_name': game_name, 'status': 'Parse failed'}
                for field_name in self.fields_to_extract.keys():
                    if field_name == 'detail_url':
                        result[field_name] = detail_url
                    else:
                        result[field_name] = ''
                results.append(result)
                self.current_results.append(result)
            
            # Save progress periodically (every 10 games)
            if len(self.current_results) % 10 == 0:
                logger.info(f"Saving progress after {len(self.current_results)} games...")
                self.save_to_excel(self.current_results)
                # Clear current results after saving to prevent duplication
                self.current_results = []
            
            # Delay between requests to be respectful
            time.sleep(delay)
        
        return results
    
    def save_to_excel(self, data):
        """Save data to Excel file, appending if file exists"""
        if not data:
            logger.warning("No data to save")
            return False
        
        try:
            df_new = pd.DataFrame(data)
            
            if os.path.exists(self.output_file):
                # Append to existing file
                df_existing = pd.read_excel(self.output_file)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                df_combined.to_excel(self.output_file, index=False)
                logger.info(f"Appended {len(df_new)} games to existing {self.output_file}")
            else:
                # Create new file
                df_new.to_excel(self.output_file, index=False)
                logger.info(f"Created new {self.output_file} with {len(df_new)} games")
            
            return True
            
        except Exception as e:
            logger.error(f"Error saving to Excel: {e}")
            return False
    
    def run(self):
        """Main method to run the parser"""
        logger.info("Starting Steam parser...")
        
        # Load existing games to avoid duplicates
        self.load_existing_games()
        
        # Read games list
        games = self.read_games_list()
        if not games:
            logger.error("No games to process")
            return
        
        # Process games
        results = self.process_games(games)
        
        # Save results
        if results:
            self.save_to_excel(results)
            logger.info(f"Processing complete. Processed {len(results)} games.")
        else:
            logger.info("No new games processed.")



if __name__ == "__main__":
    parser = SteamParser()
    parser.run()
    
    input("Нажмите Enter чтобы выйти")

