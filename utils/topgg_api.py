import requests
import time
import os
from typing import Any, Dict

class TopGGAPI:
    def __init__(self, token: str, base_url: str = "https://top.gg/api", bot_id: int = None, bypass=False):
        """Initialize the TopGG API client.
        Args:
            token (str): Your Top.gg API token.
            base_url (str): The base URL for the Top.gg API.
        """
        self.bypass = bypass
        if self.bypass is False:
            if not token:
                raise ValueError("API token is required")
            if not isinstance(token, str):
                raise TypeError("API token must be a string")
            if not base_url:
                raise ValueError("Base URL is required")
            if not isinstance(base_url, str):
                raise TypeError("Base URL must be a string")
            if bot_id is not None and not isinstance(bot_id, int):
                raise TypeError("Bot ID must be an integer")
            if bot_id is None:
                raise ValueError("Bot ID is required")
            self.token = token
            self.base_url = base_url
            self.bot_id = bot_id
            self.headers = {
                "Authorization": self.token,
                "Content-Type": "application/json"
            }
        else:
            self.token = None
            self.base_url = base_url
            self.bot_id = bot_id
            self.headers = {}

        self.cache: Dict[str, Dict[str, Any]] = {}

    def _is_cache_valid(self, endpoint: str) -> bool:
        """Check if the cache for a given endpoint is still valid."""
        if endpoint in self.cache:
            cached_time = self.cache[endpoint]['timestamp']
            if time.time() - cached_time < 30:
                return True
        return False

    def _get_cached_data(self, endpoint: str) -> Any:
        """Retrieve cached data for a given endpoint."""
        return self.cache[endpoint]['data'] if endpoint in self.cache else None

    def _set_cache(self, endpoint: str, data: Any):
        """Set cache for a given endpoint."""
        self.cache[endpoint] = {
            'data': data,
            'timestamp': time.time()
        }

    def get(self, endpoint: str, params: Dict[str, Any] = None) -> Any:
        """Perform a GET request to the Top.gg API with caching."""
        if self.bypass:
            return None

        if self._is_cache_valid(endpoint):
            return self._get_cached_data(endpoint)

        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(f"{self.base_url}/{endpoint}", headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            self._set_cache(endpoint, data)
            return data
        response.raise_for_status()
        return None

    def get_user_vote(self, user_id: int):
        """Check if a user has voted for the bot.
        Args:
            user_id (int): The ID of the user to check.
        Returns:
            bool: True if the user has voted, False otherwise.
        """
        if self.bypass:
            return True
        if not user_id:
            raise ValueError("User ID is required")
        if not isinstance(user_id, int):
            raise TypeError("User ID must be a integer")

        if int(os.getenv("OWNER_ID", 0)) == user_id:
            return True

        url = f"bots/{self.bot_id}/check"
        params = {"userId": user_id}
        if self._is_cache_valid(url):
            return self._get_cached_data(url)

        response = self.get(url, params=params)
        if response is None:
            return False
        if isinstance(response, dict) and 'voted' in response:
            voted = response['voted']
            self._set_cache(url, voted)
            return voted
        raise ValueError("Unexpected response format from Top.gg API")

    def get_vote_url(self, user_id: str = None):
        """Get the vote URL for a user.
        Args:
            user_id (str): The ID of the user to get the vote URL for.
        Returns:
            str: The vote URL for the user.
        """
        return f"https://top.gg/bot/{self.bot_id}/vote?user={user_id}" if user_id else f"https://top.gg/bot/{self.bot_id}/vote"