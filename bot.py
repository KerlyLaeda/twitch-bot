from twitchio.ext import commands
from twitchio.errors import AuthenticationError
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os
import asyncio
import logging
from dotenv import load_dotenv

# Configure logging to console and file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv()

# Twitch credentials
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_ACCESS_TOKEN = os.getenv("TWITCH_ACCESS_TOKEN")
TWITCH_REFRESH_TOKEN = os.getenv("TWITCH_REFRESH_TOKEN")
TWITCH_BROADCASTER_ID = os.getenv("TWITCH_BROADCASTER_ID")

# Google Sheets setup
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("sheet_credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
    # sheet = client.open("relaxtokenbot").sheet1
except Exception as e:  # which exception
    logging.error(f"Failed to initialize Google Sheets: {e}")
    sheet = None


def validate_token(token):
    """Check if the Access Token is valid and return seconds until expiration."""
    url = "https://id.twitch.tv/oauth2/validate"
    headers = {"Authorization": f"OAuth {token}"}
    try:
        response = requests.get(url, headers=headers)
        logging.info(f"Token validation response: {response.status_code}")
        if response.status_code == 200:
            expires_in = response.json().get("expires_in", 0)
            scopes = response.json().get("scopes", [])
            logging.info(f"Token expires in {expires_in} seconds. Scopes: {scopes}")
            return expires_in, scopes
        return 0, []
    except requests.RequestException as e:
        logging.error(f"Token validation failed: {e}")
        return 0


def refresh_access_token(refresh_token_value):
    """Refresh access token and return new tokens."""
    url = "https://id.twitch.tv/oauth2/token"
    data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value
    }
    try:
        response = requests.post(url, data=data)
        logging.info(f"Token refresh response: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            logging.info("Token refreshed successfully")
            return data["access_token"], data["refresh_token"]
        else:
            logging.error(f"Error refreshing token: {response.status_code} - {response.text}")
            logging.error("Regenerate tokens using: twitch token -u -s 'chat:read chat:edit channel:manage:redemptions channel:read:redemptions user:read:chat user:write:chat'")
            return None, None
    except requests.RequestException as e:
        logging.error(f"Token refresh failed: {e}")
        return None, None


def update_env_file(access_token, refresh_token_value):
    """Manually update .env file with new tokens."""
    try:
        env_path = os.path.abspath(".env")
        logging.info(f"Updating .env at: {env_path}")
        with open(env_path, "r") as f:
            lines = f.readlines()
        new_lines = []
        access_updated = False
        refresh_updated = False
        for line in lines:
            if line.startswith("TWITCH_ACCESS_TOKEN="):
                new_lines.append(f"TWITCH_ACCESS_TOKEN={access_token}\n")
                access_updated = True
            elif line.startswith("TWITCH_REFRESH_TOKEN="):
                new_lines.append(f"TWITCH_REFRESH_TOKEN={refresh_token_value}\n")
                refresh_updated = True
            else:
                new_lines.append(line)
        if not access_updated:
            new_lines.append(f"TWITCH_ACCESS_TOKEN={access_token}\n")
        if not refresh_updated:
            new_lines.append(f"TWITCH_REFRESH_TOKEN={refresh_token_value}\n")
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        logging.info(".env updated successfully")
    except IOError as e:
        logging.error(f"Failed to update .env: {e}")


class Bot(commands.Bot):
    def __init__(self, access_token):
        super().__init__(
            token=access_token,
            client_id=TWITCH_CLIENT_ID,
            nick="Relaxbot",  # need sep. acc 4 that
            prefix="!",
            initial_channels=["kukaraczka"]
        )
        self.access_token = access_token
        self.refresh_token_value = TWITCH_REFRESH_TOKEN

    async def event_ready(self):
        logging.info(f"Bot {self.nick} is online.")  # twitch uses my username instead of nick
        channel = self.connected_channels[0]
        await channel.send("Bot is online!")
        # noinspection PyAsyncCall
        self.loop.create_task(self.token_refresh_loop())

    async def event_message(self, message):
        if message.echo:
            return
        await self.handle_commands(message)

    async def token_refresh_loop(self):
        """Periodically check and refresh token."""
        while True:
            expires_in, scopes = validate_token(self.access_token)
            if expires_in < 300:  # Refresh if expires in 5 minutes
                new_access_token, new_refresh_token = refresh_access_token(self.refresh_token_value)
                if new_access_token and new_refresh_token:
                    self.access_token = new_access_token
                    self.refresh_token_value = new_refresh_token
                    self._connection._token = new_access_token
                    update_env_file(new_access_token, new_refresh_token)
            await asyncio.sleep(300)  # Check every 5 minutes (change to 3600)

    def get_user_points(self, username):
        """Retrieve user tokens from Google Sheet."""
        if not sheet:
            logging.error("Google Sheets not initialized")
            return None
        try:
            records = sheet.get_all_records()
            for record in records:
                if record.get("Username", "").lower() == username.lower():
                    return record.get("Tokens", 0)
            return 0
        except gspread.exceptions.APIError as e:
            logging.error(f"Google Sheets API error: {e}")
            return None
        except Exception as e:  # which exception
            logging.error(f"Error getting tokens: {e}")
            return None

    def update_user_points(self, username):
        """Update user tokens."""
        pass

    @commands.command(name="balance")
    async def check_balance(self, ctx):
        expires_in, scopes = validate_token(self.access_token)
        if expires_in < 300:
            new_access_token, new_refresh_token = refresh_access_token(self.refresh_token_value)
            if new_access_token and new_refresh_token:
                self.access_token = new_access_token
                self.refresh_token_value = new_refresh_token
                self._connection._token = new_access_token
                update_env_file(new_access_token, new_refresh_token)
            else:
                await ctx.send("Bot token expired.")
                return
        points = self.get_user_points(ctx.author.name)
        if points:
            await ctx.send(f"@{ctx.author.name}, you have {points} tokens.")
        else:
            await ctx.send(f"Error retrieving tokens for @{ctx.author.name}.")


if __name__ == "__main__":
    # Validate and refresh token before starting bot
    access_token = TWITCH_ACCESS_TOKEN
    expires_in, scopes = validate_token(access_token)
    # Regenerate if token is about to expire or missing required scopes
    if expires_in < 300 or not all(s in scopes for s in ["chat:read", "chat:edit"]):
        new_access_token, new_refresh_token = refresh_access_token(TWITCH_REFRESH_TOKEN)
        if new_access_token and new_refresh_token:
            access_token = new_access_token
            update_env_file(new_access_token, new_refresh_token)
        else:
            logging.error("Failed to refresh token at startup. Exiting.")
            exit(1)
    bot = Bot(access_token)
    bot.run()
