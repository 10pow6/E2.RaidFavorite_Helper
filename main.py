"""
API Client for automated interaction with a rate-limited service.

This module provides a robust client implementation for interacting with REST APIs
that require sophisticated rate limiting. It includes features like:

- Configurable exponential backoff
- Request rate limiting with tiered thresholds
- Jitter-based wait times to prevent thundering herd problems
- Automatic retry logic for failed requests
- Session management and connection pooling

The rate limiting strategy uses a tiered approach where wait times increase
based on the number of API calls made. This helps prevent both:
1. Short-term rate limit issues (too many requests per second)
2. Long-term rate limit issues (too many requests per minute/hour)

Example usage:
    config = APIConfig(...)
    client = APIClient(config)
    await client.run()

Rate Limiting Strategy:
    The client implements a multi-tiered rate limiting strategy:
    - Base tier: Minimum wait between requests
    - Progressive tiers: Longer waits at specific API call counts
    - Random jitter: Added to prevent request synchronization
    - Exponential backoff: For handling service errors

Configuration:
    The client reads configuration from a config.ini file which should contain:
    [PROD]
    XCSRF=...
    COOKIE=...
    REFERER=...
    R_API=...
    TILE_BREAKER_COUNT=...
    DEFAULT_WAIT=...
    WAIT_BACKOFF_MIN=...
    WAIT_BACKOFF_MAX=...
    WAIT_RANDOMNESS_MIN=...
    WAIT_RANDOMNESS_MAX=...
"""

import configparser
import asyncio
import httpx
import json
import random
from typing import Dict, Optional, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

@dataclass
class APIConfig:
    """
    Configuration settings for the API client.

    This dataclass encapsulates all configuration parameters needed for the API client,
    including authentication, endpoints, and rate limiting parameters.

    Attributes:
        xcsrf (str): CSRF token for API authentication
        cookie (str): Authentication cookie
        referer (str): Referer header value
        r_api (str): Base API URL
        tile_breaker_count (int): Threshold for tile processing
        default_wait (float): Default wait time between requests in milliseconds
        wait_backoff_min (float): Minimum backoff time for failed requests in milliseconds
        wait_backoff_max (float): Maximum backoff time for failed requests in milliseconds
        wait_randomness_min (float): Minimum random jitter in milliseconds
        wait_randomness_max (float): Maximum random jitter in milliseconds
    """
    xcsrf: str
    cookie: str
    referer: str
    r_api: str
    tile_breaker_count: int
    default_wait: float
    wait_backoff_min: float
    wait_backoff_max: float
    wait_randomness_min: float
    wait_randomness_max: float

class APIClient:
    def __init__(self, config: APIConfig):
        self.config = config
        self.api_call_counter = 0
        self.land_page = -1
        self.favorite_landfields = []  # Store landfield IDs for bulk upsert
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-ch-ua": "\"Google Chrome\";v=\"117\", \"Not;A=Brand\";v=\"8\", \"Chromium\";v=\"117\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "x-csrftoken": config.xcsrf,
            "x-snoopingaround": "If you enjoy learning how things work, have a look at earth2.io/careers and contact us. Mention this header.",
            "cookie": config.cookie,
            "Referer": config.referer,
            "Referrer-Policy": "strict-origin-when-cross-origin"
        }

    async def bulk_upsert_favorites(self) -> dict:
        """Bulk upsert favorite landfields."""
        if not self.favorite_landfields:
            print("No landfields to upsert")
            return {}

        self.api_call_counter += 1
        
        async def call_api(client):
            # Deduplicate landfield IDs
            unique_landfields = list(set(self.favorite_landfields))
            
            payload = {
                "user_favorite_landfields": [
                    {
                        "color": "white",
                        "landfield_id": landfield_id
                    }
                    for landfield_id in unique_landfields
                ]
            }
            
            print(f"\n>>> Making PUT request for bulk upsert of {len(unique_landfields)} landfields")
            response = await client.put(
                "/user_favorite_landfields/bulk_upsert",
                headers=self.headers,
                json=payload
            )
            
            raw_content = await response.aread()
            status = response.status_code
            headers = dict(response.headers)
            
            print(">>> BEGIN BULK UPSERT RESPONSE DATA <<<")
            print(f"Status Code: {status}")
            print(f"Raw Content Length: {len(raw_content)}")
            try:
                text_content = raw_content.decode('utf-8', errors='replace')
            except Exception as e:
                print(f"Failed to decode content: {e}")
            print(">>> END BULK UPSERT RESPONSE DATA <<<")
            
            return {
                'status': status,
                'headers': headers,
                'content': raw_content
            }
        
        try:
            response_data = await self.api_call_with_backoff(call_api)
            if not response_data['content']:
                print("Warning: Empty bulk upsert response content")
                return {}
            return json.loads(response_data['content'])
        except Exception as e:
            print(f"Error in bulk_upsert_favorites: {type(e).__name__}: {str(e)}")
            raise

    def calculate_wait_time(self, api_calls: int) -> float:
        """
        Calculate wait time based on number of API calls with exponential backoff.
        Returns time in seconds.
        """
        # Define wait tiers with thresholds and corresponding wait times (in seconds)
        wait_tiers = [
            (2000, 131.072),  # 131072ms
            (1000, 65.536),   # 65536ms
            (500, 32.768),    # 32768ms
            (300, 16.384),    # 16384ms
            (150, 16.384),
            (100, 16.384),
            (64, 8.192),      # 8192ms
            (50, 8.192),
            (32, 4.096),      # 4096ms
            (20, 4.096),
            (16, 2.048),      # 2048ms
            (10, 2.048),
            (8, 1.024)        # 1024ms
        ]

        # Find appropriate wait time based on API calls
        for threshold, wait_time in wait_tiers:
            if api_calls % threshold == 0:
                return wait_time
        
        # Default to configured wait time if no threshold matches
        return self.config.default_wait / 1000

    def add_jitter(self, base_wait: float) -> float:
        """
        Add random jitter to wait time to prevent synchronized API calls.
        """
        jitter = random.randint(self.config.wait_randomness_min, 
                              self.config.wait_randomness_max) / 1000
        return base_wait + jitter

    async def wait_helper(self, level: int = 0) -> None:
        """
        Calculate and execute wait period between API calls.
        Includes exponential backoff and jitter for better rate limiting.
        """
        if self.api_call_counter < 1:
            return

        base_wait = self.calculate_wait_time(self.api_call_counter)
        total_wait = self.add_jitter(base_wait)
        
        indent = "    " * level
        print(f"{indent}~~~~ Waiting for {total_wait:.3f}s ~~~~")
        await asyncio.sleep(total_wait)

    @asynccontextmanager
    async def api_client(self):
        """
        Context manager for handling API client sessions.
        
        Creates and manages an HTTP client session with proper connection pooling
        and automatic connection cleanup.
        
        Yields:
            httpx.AsyncClient: Configured HTTP client for making API requests
            
        Example:
            async with self.api_client() as client:
                response = await client.get(url)
        """
        async with httpx.AsyncClient(base_url=self.config.r_api) as client:
            yield client

    def calculate_exponential_backoff(self, attempt: int, base_delay: float = 1.0) -> float:
        """
        Calculate exponential backoff time with jitter.
        
        Args:
            attempt: The current retry attempt number (0-based)
            base_delay: Base delay in seconds
            
        Returns:
            Float representing seconds to wait
        """
        exp_delay = base_delay * (2 ** attempt)  # exponential increase
        jitter = random.uniform(0, 0.1 * exp_delay)  # 10% jitter
        return min(exp_delay + jitter, 30.0)  # cap at 30 seconds
        
    async def api_call_with_backoff(self, call_func, *args, **kwargs):
        """
        Execute an API call with automatic retry and exponential backoff.

        Implements sophisticated error handling with different strategies for
        different types of errors:
        - Rate limiting (429): Exponential backoff with jitter
        - Server errors (5xx): Exponential backoff with jitter
        - Client errors (4xx): Fail fast for client errors except 429
        - Network errors: Retry with backoff
        - JSON decode errors: Retry with backoff

        Args:
            call_func: Async function that makes the actual API call
            *args: Positional arguments to pass to call_func
            **kwargs: Keyword arguments to pass to call_func

        Returns:
            The JSON response from the API call

        Raises:
            httpx.HTTPStatusError: On unrecoverable HTTP errors
            Exception: If all retry attempts fail
        """
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                async with self.api_client() as client:
                    response = await call_func(client, *args, **kwargs)
                    
                    # If it's already a dict, it was pre-processed by the call function
                    if isinstance(response, dict):
                        return response
                        
                    # Ensure we got a valid response
                    if not response.content:
                        print(f"Empty response received (Status: {response.status_code})")
                        if attempt < max_retries - 1:
                            continue
                        raise httpx.HTTPError(f"Empty response with status {response.status_code}")
                    
                    # First check if response indicates an error
                    response.raise_for_status()
                    
                    # Try to decode JSON
                    try:
                        return response.json()
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error on content: {response.content[:100]}...")
                        print(f"Status code was: {response.status_code}")
                        print(f"Headers: {dict(response.headers)}")
                        if attempt < max_retries - 1:
                            continue
                        raise
                    
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                
                # Handle rate limiting and server errors with retry
                if status_code == 429 or 500 <= status_code < 600:
                    if attempt < max_retries - 1:
                        delay = self.calculate_exponential_backoff(attempt)
                        print("=" * 80)
                        print(f"HTTP {status_code} error occurred. Details:")
                        print(f"URL: {e.request.url}")
                        print(f"Attempt: {attempt + 1}/{max_retries}")
                        print(f"Retrying in {delay:.2f} seconds...")
                        print("=" * 80)
                        await asyncio.sleep(delay)
                        continue
                        
                # Other client errors should fail fast
                if 400 <= status_code < 500 and status_code != 429:
                    print(f"Client error {status_code} occurred: {e}")
                    raise
                    
            except httpx.RequestError as e:
                # Network-level errors
                if attempt < max_retries - 1:
                    delay = self.calculate_exponential_backoff(attempt)
                    print("=" * 80)
                    print(f"Network error occurred: {e}")
                    print(f"Request URL: {e.request.url}")
                    print(f"Attempt: {attempt + 1}/{max_retries}")
                    print(f"Retrying in {delay:.2f} seconds...")
                    print("=" * 80)
                    await asyncio.sleep(delay)
                    continue
                raise
                
            except Exception as e:
                # Unexpected errors
                if attempt < max_retries - 1:
                    delay = self.calculate_exponential_backoff(attempt)
                    print("=" * 80)
                    print(f"Unexpected error: {type(e).__name__}: {e}")
                    print(f"Attempt: {attempt + 1}/{max_retries}")
                    print(f"Retrying in {delay:.2f} seconds...")
                    print("=" * 80)
                    await asyncio.sleep(delay)
                    continue
                raise
                
        raise Exception(f"Max retries ({max_retries}) exceeded")
    
    async def get_landfield(self, page: int) -> dict:
        """Get landfield data for a specific page."""
        self.api_call_counter += 1
        
        async def call_api(client):
            print(f"\n>>> Making GET request for landfield page {page}")
            response = await client.get(
                f"/droids/landfields?page={page}&q=&sortDir=asc",
                headers=self.headers
            )
            # Get raw data before any processing
            raw_content = await response.aread()
            status = response.status_code
            headers = dict(response.headers)
            
            print(">>> BEGIN LANDFIELD RESPONSE DATA <<<")
            print(f"Status Code: {status}")
            #print(f"Headers: {headers}")
            print(f"Raw Content Length: {len(raw_content)}")
            #print(f"Raw Content: {raw_content!r}")
            try:
                text_content = raw_content.decode('utf-8', errors='replace')
                #print(f"Decoded Content: {text_content!r}")
            except Exception as e:
                print(f"Failed to decode content: {e}")
            print(">>> END LANDFIELD RESPONSE DATA <<<")
            
            return {
                'status': status,
                'headers': headers,
                'content': raw_content
            }
            
        try:
            response_data = await self.api_call_with_backoff(call_api)
            if not response_data['content']:
                print("Warning: Empty landfield response content")
                return {}
            return json.loads(response_data['content'])
        except Exception as e:
            print(f"Error in get_landfield: {type(e).__name__}: {str(e)}")
            raise

    async def get_attack_targets(self, landfield: str, page: int, droids_url_str: str) -> dict:
        """Get attack targets for a specific landfield."""
        self.api_call_counter += 1
        
        async def call_api(client):
            print(f"\n>>> Making GET request for attack targets (landfield: {landfield}, page: {page})")
            response = await client.get(
                f"/droids/landfields/{landfield}/raid?autos=false&favorites=false&page={page}"
                f"&q=&recents=false&sortBy=tilesCount&sortDir=desc&{droids_url_str}",
                headers=self.headers
            )

            
            # Get raw data before any processing
            raw_content = await response.aread()
            status = response.status_code
            headers = dict(response.headers)
            
            print(">>> BEGIN ATTACK TARGETS RESPONSE DATA <<<")
            print(f"Status Code: {status}")
            #print(f"Headers: {headers}")
            print(f"Raw Content Length: {len(raw_content)}")
            #print(f"Raw Content: {raw_content!r}")
            try:
                text_content = raw_content.decode('utf-8', errors='replace')
                #print(f"Decoded Content: {text_content!r}")
            except Exception as e:
                print(f"Failed to decode content: {e}")
            print(">>> END ATTACK TARGETS RESPONSE DATA <<<")
            
            return {
                'status': status,
                'headers': headers,
                'content': raw_content
            }
            
        try:
            response_data = await self.api_call_with_backoff(call_api)
            if not response_data['content']:
                print("Warning: Empty attack targets response content")
                return {}
            return json.loads(response_data['content'])
        except Exception as e:
            print(f"Error in get_attack_targets: {type(e).__name__}: {str(e)}")
            raise

    async def toggle_favorite(self, at_landfield: str) -> dict:
        """Toggle favorite status for a landfield."""
        self.api_call_counter += 1
        
        async def call_api(client):
            print(f"\n>>> Making PUT request to toggle favorite for landfield: {at_landfield}")
            response = await client.put(
                f"/landfields/{at_landfield}/toggle_favorite",
                headers=self.headers
            )
            # Get raw response data before attempting anything else
            raw_content = await response.aread()
            status = response.status_code
            headers = dict(response.headers)
            
            print(">>> BEGIN TOGGLE FAVORITE RESPONSE DATA <<<")
            print(f"Status Code: {status}")
            #print(f"Headers: {headers}")
            print(f"Raw Content Length: {len(raw_content)}")
            #print(f"Raw Content: {raw_content!r}")
            try:
                text_content = raw_content.decode('utf-8', errors='replace')
                #print(f"Decoded Content: {text_content!r}")
            except Exception as e:
                print(f"Failed to decode content: {e}")
            print(">>> END TOGGLE FAVORITE RESPONSE DATA <<<")
            
            # Return the raw data for processing
            return {
                'status': status,
                'headers': headers,
                'content': raw_content
            }
        
        try:
            response_data = await self.api_call_with_backoff(call_api)
            if not response_data['content']:
                print("Warning: Empty toggle favorite response content")
                return {}
            return json.loads(response_data['content'])
        except Exception as e:
            print(f"Error in toggle_favorite: {type(e).__name__}: {str(e)}")
            raise

    async def process_property(self, property: dict) -> None:
        property_id = property["id"]
        property_description = property["attributes"]["description"]
        print(f"<< Processing {property_id} | {property_description}>>")

        droid_ids = property['meta']['droidIds']
        if not droid_ids:
            print("\t<< No droids >>")
            return

        droids_url_str = "&withinRangeDroidIds[]=".join(droid_ids)
        droids_url_str = "withinRangeDroidIds[]=" + droids_url_str

        # Get initial payload and total pages
        initial_payload = await self.get_attack_targets(property_id, 1, droids_url_str)
        await self.wait_helper()
        total_at_pages = initial_payload["meta"]["pages"]

        async def process_single_page(page_payload):
            for target in page_payload['data']:
                target_meta = target['meta']
                target_attrs = target["attributes"]
                
                at_droids = target_meta['numberOfDroids']
                at_civs = target_meta['numberOfCivilians']
                at_favorited = target_meta['favorite']
                at_landfield_id = target["id"]
                at_landfield_description = target_attrs["description"]
                at_tile_count = target_attrs["tileCount"]
                at_landfield_tier = target_attrs['landfieldTier']

                print(
                    f"\t<< Processing A.T. {at_landfield_id} | {at_landfield_description}: "
                    f"[ TIER: {at_landfield_tier}"
                    f" | TILES: {at_tile_count}"
                    f" | CIVS: {at_civs}"
                    f" | DROIDS: {at_droids}"
                    f" | FAVORITE: {at_favorited}]"
                )

                if at_tile_count < self.config.tile_breaker_count:
                    print("\t\t<< Breaker threshold reached >>")
                    return False

                if at_landfield_tier == 1 and at_droids == 0 and at_civs == 0 and at_favorited is None:
                    print(f"\t\t|| Favorite Added >> {at_landfield_id} | {at_landfield_description}")
                    self.favorite_landfields.append(at_landfield_id)

            return True

        # Process all pages
        for page_num in range(1, total_at_pages + 1):
            current_payload = initial_payload if page_num == 1 else await self.get_attack_targets(property_id, page_num, droids_url_str)
            
            if page_num > 1:
                print(f"\t++ A.T. Page Flip to Page {page_num} ++")
                await self.wait_helper(level=1)
            
            if not await process_single_page(current_payload):
                break

        # Handle bulk upsert
        if self.favorite_landfields:
            print(f"<< Performing bulk upsert for {len(self.favorite_landfields)} landfields >>")
            await self.bulk_upsert_favorites()
            await self.wait_helper(level=2)
            self.favorite_landfields = []
        else:
            print(f"<< Skipping bulk upsert for {property_id} | {property_description} as no favorite candidates were found >>")

    async def run(self):
        """
        Main execution loop for the API client.

        This method orchestrates the entire API interaction process:
        1. Fetches initial landfield data
        2. Processes each page of results
        3. Handles property processing for each item
        4. Manages API call counting and rate limiting

        The method implements pagination handling and maintains state about the
        current processing position in case of interruption.

        Note:
            - Resets API call counter between pages
            - Maintains last processed page number for recovery
            - Implements wait periods between operations
        """
        payload = await self.get_landfield(1)

        await self.wait_helper()
        
        total_pages = payload['meta']['pages']
        print(f"<< Total Raid Property Pages: {total_pages} >>")
        
        for i in range(1, total_pages + 1):
            for property in payload['data']:
                await self.process_property(property)
            
            if i + 1 <= total_pages:
                self.land_page = i
                print("+" * 80)
                print(f"+++++++++++++ Landfield Page Flip to Page {i + 1}")
                print("+" * 80)
                payload = await self.get_landfield(i + 1)
                await self.wait_helper()
                self.api_call_counter = 0

async def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    
    api_config = APIConfig(
        xcsrf=config['PROD']['XCSRF'],
        cookie=config['PROD']['COOKIE'],
        referer=config['PROD']['REFERER'],
        r_api=config['PROD']['R_API'],
        tile_breaker_count=int(config['PROD']['TILE_BREAKER_COUNT']),
        default_wait=int(config['PROD']['DEFAULT_WAIT']),
        wait_backoff_min=int(config['PROD']['WAIT_BACKOFF_MIN']),
        wait_backoff_max=int(config['PROD']['WAIT_BACKOFF_MAX']),
        wait_randomness_min=int(config['PROD']['WAIT_RANDOMNESS_MIN']),
        wait_randomness_max=int(config['PROD']['WAIT_RANDOMNESS_MAX'])
    )
    
    client = APIClient(api_config)
    await client.run()

if __name__ == "__main__":
    asyncio.run(main())