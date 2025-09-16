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
        
        if sessionid and steam_login_secure:
            # Set cookies for authenticated requests
            self.session.cookies.set('sessionid', sessionid, domain='store.steampowered.com')
            self.session.cookies.set('steamLoginSecure', steam_login_secure, domain='store.steampowered.com')
            
            # Set optional cookies if available
            steam_language = os.getenv('STEAM_LANGUAGE')
            steam_timezone_offset = os.getenv('STEAM_TIMEZONE_OFFSET')
            
            if steam_language:
                self.session.cookies.set('Steam_Language', steam_language, domain='store.steampowered.com')
            if steam_timezone_offset:
                self.session.cookies.set('timezoneOffset', steam_timezone_offset, domain='store.steampowered.com')
            
            logger.info("Steam authentication cookies loaded from environment variables")
        else:
            logger.warning("STEAM_SESSIONID or STEAM_LOGIN_SECURE not found in environment variables")
            logger.warning("Running without authentication - age verification may be required")
            
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
            if self._is_age_verification_page(response):
                logger.info(f"Age verification required for {detail_url}")
                if self._handle_age_verification(response):
                    # Retry the original request after age verification
                    response = self.session.get(detail_url, timeout=10)
                    response.raise_for_status()
                else:
                    logger.warning(f"Failed to bypass age verification for {detail_url}")
                    return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract game details
            game_data = {
                'price': self._extract_price(soup),
                'release_date': self._extract_release_date(soup),
                'detail_url': detail_url
            }
            
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
                
            return "Price not found"
            
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
            
            return "Release date not found"
            
        except Exception as e:
            logger.warning(f"Error extracting release date: {e}")
            return "Error extracting release date"
    
    def _format_date(self, date_string):
        """Format date from '16 Feb, 2012' to '16.02.2012'"""
        try:
            # Month mapping
            month_map = {
                'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
                'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
                'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
            }
            
            # Remove any extra spaces and commas
            date_string = date_string.replace(',', '').strip()
            
            # Split into parts
            parts = date_string.split()
            
            if len(parts) == 3:
                # Format like "16 Feb 2012"
                day, month, year = parts
            elif len(parts) == 2:
                # Format like "Feb 2012" - assume day is 1st
                month, year = parts
                day = '01'
            else:
                return date_string  # Return original if format not recognized
            
            # Check if month is valid abbreviation
            month_cap = month.capitalize()
            if month_cap not in month_map:
                return date_string  # Return original if month not recognized
            
            # Convert month abbreviation to number
            month_num = month_map[month_cap]
            
            # Validate day and year
            if not day.isdigit() or not year.isdigit():
                return date_string
                
            # Format as dd.mm.yyyy
            day = day.zfill(2)
            return f"{day}.{month_num}.{year}"
            
        except Exception:
            # Return original string if formatting fails
            return date_string
    
    def _is_age_verification_page(self, response):
        """Check if the response contains an age verification page"""
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for multiple age verification indicators
            age_indicators = [
                # Russian age verification text
                soup.find('h2', string=re.compile(r'пожалуйста.*укажите.*дату.*рождения', re.IGNORECASE)),
                soup.find(string=re.compile(r'возраст.*проверка', re.IGNORECASE)),
                
                # English age verification text
                soup.find('h2', string=re.compile(r'please.*enter.*date.*birth', re.IGNORECASE)),
                soup.find(string=re.compile(r'age.*check', re.IGNORECASE)),
                
                # Age verification form
                soup.find('form', {'action': re.compile(r'agecheckset')}),
                soup.find('form', id=re.compile(r'agecheck')),
                
                # Age gate elements
                soup.find('div', id=re.compile(r'agegate')),
                soup.find('div', class_=re.compile(r'agegate'))
            ]
            
            # Check if any indicator is found
            return any(indicator is not None for indicator in age_indicators)
        except Exception:
            return False
    
    def _handle_age_verification(self, response):
        """Handle Steam age verification by submitting the form"""
        try:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract the age verification form
            form = soup.find('form', {'action': re.compile(r'agecheckset')})
            if not form:
                logger.warning("No age verification form found")
                return False
            
            # Extract form action URL
            action_url = form.get('action')
            if not action_url.startswith('http'):
                # Make absolute URL
                action_url = f"https://store.steampowered.com{action_url}"
            
            # Extract sessionid from cookies or form
            sessionid = None
            if 'sessionid' in self.session.cookies:
                sessionid = self.session.cookies['sessionid']
            else:
                # Try to find sessionid in form inputs
                sessionid_input = form.find('input', {'name': 'sessionid'})
                if sessionid_input:
                    sessionid = sessionid_input.get('value')
            
            if not sessionid:
                logger.warning("Could not find sessionid for age verification")
                return False
            
            # Prepare form data (using a valid date of birth - 16 August 1995)
            form_data = {
                'sessionid': sessionid,
                'ageDay': '16',
                'ageMonth': 'August',
                'ageYear': '1995'
            }
            
            # Submit the form
            logger.info(f"Submitting age verification to {action_url}")
            age_response = self.session.post(action_url, data=form_data, timeout=10)
            age_response.raise_for_status()
            
            # Check if age verification was successful
            if age_response.status_code == 200:
                logger.info("Age verification successful")
                return True
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
                result = {
                    'game_name': game_name,
                    'detail_url': 'Not found',
                    'price': 'Not found',
                    'release_date': 'Not found',
                    'status': 'Search failed'
                }
                results.append(result)
                self.current_results.append(result)
                continue
            
            # Parse game details
            game_data = self.parse_game_details(detail_url)
            if game_data:
                result = {
                    'game_name': game_name,
                    'detail_url': detail_url,
                    'price': game_data.get('price', 'Not found'),
                    'release_date': game_data.get('release_date', 'Not found'),
                    'status': 'Success'
                }
                results.append(result)
                self.current_results.append(result)
            else:
                result = {
                    'game_name': game_name,
                    'detail_url': detail_url,
                    'price': 'Parse failed',
                    'release_date': 'Parse failed',
                    'status': 'Parse failed'
                }
                results.append(result)
                self.current_results.append(result)
            
            # Save progress periodically (every 10 games)
            if len(self.current_results) % 10 == 0:
                logger.info(f"Saving progress after {len(self.current_results)} games...")
                self.save_to_excel(self.current_results)
            
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

# Example of extending parameters
def add_custom_parameter(parser, parameter_name, extractor_function):
    """Example function to show how to extend parameters"""
    # This would need to be integrated into the parsing logic
    pass

if __name__ == "__main__":
    parser = SteamParser()
    parser.run()
