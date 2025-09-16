# Steam Game Parser

A Python script to parse Steam game information from a list of game names.

## Features

- Parses games from `games_list.txt` file
- Searches Steam store for each game
- Extracts price and release date information
- Handles age verification automatically
- Supports authentication via Steam cookies
- Saves results to Excel file (`steam_games.xlsx`)
- Avoids duplicates by checking existing entries

## Installation

1. Clone or download this repository
2. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Basic Usage (without authentication)

```bash
python steam_parser.py
```

### Authentication Setup

For better results (especially with age-restricted games), set up Steam authentication:

1. **Create `.env` file**:
   ```bash
   cp .env.example .env
   ```

2. **Get Steam cookies**:
   - Log into Steam in your web browser
   - Open Developer Tools (F12)
   - Go to Application/Storage tab
   - Find cookies for `store.steampowered.com`
   - Copy the values for `sessionid` and `steamLoginSecure`

3. **Edit `.env` file**:
   ```
   STEAM_SESSIONID=your_actual_session_id_here
   STEAM_LOGIN_SECURE=your_actual_steam_login_secure_here
   STEAM_PARENTAL=your_actual_steam_parental_cookie (if set)
   ```

## Build for your friends
```
python3 -m PyInstaller --onefile steam_parser.py
```

## Input File Format

Create a `games_list.txt` file with one game name per line:

```
Game Name 1
Game Name 2
Another Game
```

## Output

The script creates/updates `steam_games.xlsx` with columns:
- `game_name`: Original game name from list
- `detail_url`: Steam store URL
- `price`: Game price
- `release_date`: Release date (formatted as dd.mm.yyyy)
- `status`: Processing status
etc.

## Error Handling

- **Age Verification**: Automatically handles Steam age verification
- **Network Errors**: Retries and continues processing
- **Duplicate Games**: Skips games already in Excel file
- **Graceful Exit**: Saves progress on Ctrl+C interruption

## Extending Parameters

To add more parameters, modify the `parse_game_details` method and add new extraction functions.

## License

MIT License
