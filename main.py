import configparser
import asyncio
import httpx
import json
import random
from decimal import Decimal
import threading
import os
import msvcrt

config=configparser.ConfigParser()
config.read('config.ini')

new_favorites_added = False  # Global variable to track new favorites

SECRET_XCSRF=config['PROD']['XCSRF']
SECRET_COOKIE=config['PROD']['COOKIE']
HEADER_REFERER=config['PROD']['REFERER']
R_API=config['PROD']['R_API']
TILE_BREAKER_COUNT=int(config['PROD']['TILE_BREAKER_COUNT'])
DEFAULT_WAIT=int(config['PROD']['DEFAULT_WAIT'])
WAIT_BACKOFF_MIN=int(config['PROD']['WAIT_BACKOFF_MIN'])
WAIT_BACKOFF_MAX=int(config['PROD']['WAIT_BACKOFF_MAX'])
WAIT_RANDOMNESS_MIN=int(config['PROD']['WAIT_RANDOMNESS_MIN'])
WAIT_RANDOMNESS_MAX=int(config['PROD']['WAIT_RANDOMNESS_MAX'])
API_CALL_COUNTER=0
LAND_PAGE=-1


headers = {
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
    "x-csrftoken": SECRET_XCSRF,
    "x-snoopingaround": "If you enjoy learning how things work, have a look at earth2.io/careers and contact us. Mention this header.",
    "cookie": SECRET_COOKIE,
    "Referer": HEADER_REFERER,
    "Referrer-Policy": "strict-origin-when-cross-origin"
}

'''
    n: Number to return a wait time for
    wait_mapping {ordered list}: Mapping in format of check_value:wait_time ; default set for user; order from largest check_value to lowest
'''
def find_divisor_mapping(
        n, 
        wait_mapping = {
            2000:131072,
            1000:65536,
            500:32768,
            300:16384,
            256:16384,
            150:16384,
            100:16384,
            64:8192,
            50:8192,
            32:4096,
            20:4096,
            16:2048,
            10:2048,
            8:1024  
        }
):
    for k, wait_time in wait_mapping.items():
        if n%k  == 0:
            return wait_time/1000 # 1000 ms in a second; asyncio sleep expects this
    return -1

'''
    counter: iteration of an event
    defaultWait: default amount of time to wait for an action should there be no override
'''
async def wait_helper( level=0,default_wait=DEFAULT_WAIT/1000):
    counter=API_CALL_COUNTER

    if( counter < 1 ):
        return

    divisor_mapping = find_divisor_mapping( counter )
    wait_time = default_wait if divisor_mapping == -1 else divisor_mapping
    wait_time += random.randint(WAIT_RANDOMNESS_MIN,WAIT_RANDOMNESS_MAX)/1000

    added_tabs=""
    for i in range(0,level):
        added_tabs+="\t"
    message = added_tabs + "~~~~ Waiting for " + str(wait_time) + "s ~~~~"
    print(message)
    await asyncio.sleep(wait_time)



def api_with_backoff(func):
    async def wrapper(*args, **kwargs):
        tries=3

        for i in range(tries):
            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                if i < tries - 1: # i is zero indexed
                    print("********************************************************************************")
                    print("*************         Catastrophic API Failure :(                  *************")
                    wait_time=random.randint(WAIT_BACKOFF_MIN,WAIT_BACKOFF_MAX)/1000
                    print("********************************************************************************")
                    print("************* API CALL ERROR EXPERIENCED, BACKING OFF AND RETRYING")
                    print("************* RETRY #" + str(i))
                    print("************* LONG WAIT OF " + str(wait_time) + "s")
                    print("************* LAST PAGE CAPTURED OF " + str(LAND_PAGE) )
                    print("********************************************************************************")
                    await asyncio.sleep(wait_time)

                    continue
                else:
                    raise
            break

        return result
    return wrapper

async def call_landfield(page, batch_size=50):  # Added batch_size parameter
    global API_CALL_COUNTER
    API_CALL_COUNTER += 1

    api_call = f"/droids/landfields?page={page}&q=&sortDir=asc&limit={batch_size}" 

    @api_with_backoff
    async def call_api():
        async with httpx.AsyncClient(base_url=R_API, timeout=60.0) as client:
            r = await client.get(api_call, headers=headers)
        return r

    r = await call_api()
    return r.json()

async def call_get_attack_targets(landfield,page,droids_url_str):
    global API_CALL_COUNTER
    API_CALL_COUNTER+=1

    api_call = f"/droids/landfields/{landfield}/raid?autos=false&favorites=false&page={page}&q=&recents=false&sortBy=tilesCount&sortDir=desc&{droids_url_str}&include=promisedEssenceBalance,claimedEssenceBalance"

    @api_with_backoff
    async def call_api():
        async with httpx.AsyncClient(base_url=R_API, timeout=60.0) as client:
            r = await client.get(api_call, headers=headers) 
        return r
      
    r = await call_api()

    return r.json()

async def call_api(url):
    global API_CALL_COUNTER
    API_CALL_COUNTER += 1

    @api_with_backoff  
    async def call_api_internal():
        async with httpx.AsyncClient(base_url=R_API, timeout=60.0) as client:
            response = await client.get(url, headers=headers)
        return response

    # Introduce a random delay between 1 and 3 seconds
    delay = random.uniform(1, 10)  # Adjust the range as needed
    await asyncio.sleep(delay)

    response = await call_api_internal()
    return response.json()

async def call_favorite_bulk(landfield_ids):  
    global API_CALL_COUNTER
    API_CALL_COUNTER += 1

    api_call = "/user_favorite_landfields/bulk_upsert"
    payload = {
        "user_favorite_landfields": [
            {"color": "white", "landfield_id": landfield_id} for landfield_id in landfield_ids
        ]
    }

    @api_with_backoff
    async def call_api():
        try:
            async with httpx.AsyncClient(base_url=R_API, timeout=60.0) as client:
                r = await client.put(api_call, headers=headers, json=payload)  # Use content= for JSON data
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            print(f"Error adding favorites in bulk (HTTP status): {e}")
        except httpx.RequestError as e:
            print(f"Error adding favorites in bulk (request error): {e}")
        except Exception as e:
            print(f"Unexpected error adding favorites in bulk: {e}")

    response = await call_api()

    return response  # Return the response or None if an error occurred

async def favorite_processing(property):
    global skip_to_next, new_favorites_added
    new_favorites_added = False
    property_id = property["id"]
    property_description = property["attributes"]["description"]
    print(f"<< Processing {property_id}: {property_description} >>")
    droid_ids = property['meta']['droidIds']
    if len(droid_ids) > 0:
        droids_url_str = "withinRangeDroidIds[]=" + "&withinRangeDroidIds[]=".join(droid_ids)
    else:
        print("\t<< No droids >>")
        return  # nothing to process, no droids

    try:
        payload = await call_get_attack_targets(property_id, 1, droids_url_str)
        await wait_helper()
        total_at_pages = payload["meta"]["pages"]

        for i in range(1, total_at_pages + 1):
            targets = payload['data']
            for target in targets:
                    try:
                        at_droids=target['meta']['numberOfDroids']
                        at_civs=target['meta']['numberOfCivilians']
                        at_favorited=target['meta']['favorite']
                        at_landfield_id=target["id"]
                        at_owner_id=target["attributes"]["ownerId"]
                        at_description=target["attributes"]["description"]
                        at_tile_tier=target["attributes"]["tileClass"]
                        at_tile_count=target["attributes"]["tileCount"]
                        at_is_raided=target["meta"]["isRaided"]

                        # Fetch landfield details
                        landfield_details_url = f"{R_API}/landfields/{at_landfield_id}"  # Assuming R_API is defined
                        landfield_details = await call_api(landfield_details_url)  # Use a helper function to make API calls

                        # Access promisedEssenceBalance from landfield details
                        promised_essence = Decimal(landfield_details["promisedEssenceBalance"])
                        claimed_essence = Decimal(landfield_details["claimedEssenceBalance"])

                        processing_str = "\t<< Processing A.T. " + str(at_landfield_id) + ": "
                        processing_str += "[ TIER: "+str(at_tile_tier)
                        processing_str += " | TILES: "+str(at_tile_count) 
                        processing_str += " | CIVS: " + str(at_civs) 
                        processing_str += " | DROIDS: " + str(at_droids) 
                        processing_str += " | FAVORITE: " + str(at_favorited) + "]"
                        print( processing_str )
                        # break out of loop, we raided recently (assumed mihaj script)
                        if( at_is_raided ):
                            print("\t\t<< Recently raided >>")
                            continue
                        # break out of loop, we reached our breaker
                        if( at_tile_count < TILE_BREAKER_COUNT ):
                            print("\t\t<< Breaker threshold reached >>")
                            return
                        if (
                            at_droids == 0
                            and at_civs == 0
                            and at_favorited is None
                            and (promised_essence > 0 or claimed_essence < 1)
                            and at_is_raided is False
                        ):
                            favorite_payload = await call_favorite_bulk(at_landfield_id)
                            print("\t\t" + "|| Favorite Added || " + str(favorite_payload))
                            new_favorites_added = True
                            await wait_helper(level=2)
                            
                    except Exception as e:
                        print(f"\t\tError processing target {target['id']}: {e}")
                        continue  # Skip to the next target

            if (i + 1) < total_at_pages:
                print(f"\t++ A.T. Page Flip to Page {i + 1} ++")
                payload = await call_get_attack_targets(property_id, i + 1, droids_url_str)
                await wait_helper(level=1)
                
    except Exception as e:
        print(f"Error fetching attack targets for property {property_id}: {e}")

    if new_favorites_added == True:
        with open("new_favorites.txt", "a") as f:
            f.write(f"{property_id} - {property_description}\n")

def input_thread():
    global skip_to_next
    while True:
        if msvcrt.kbhit() and msvcrt.getch() == b'y':
            skip_to_next = True
            break  # Exit the loop if 'y' is entered

async def main():
    global LAND_PAGE
    skip_to_next = False 
    threading.Thread(target=input_thread).start()  # Start the input thread

    progress_file = "progress.txt"
    start_from_property = 1  # Default starting point

    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            try:
                start_from_property = int(f.read().strip())
            except ValueError:
                print("Invalid progress file content. Starting from the beginning.")

    # Get first page details
    try:
        payload = await call_landfield(1)  # Initial call with default batch size
        await wait_helper()
    except Exception as e:
        print(f"Error fetching landfield data: {e}")
        return  # Exit if initial call fails

    total_pages = payload['meta']['pages']
    print("<< Total Raid Property Pages: " + str(total_pages) + " >>")

    all_properties = []
    batch_size = 50  # Set your desired batch size

    for i in range(1, total_pages + 1):
        try:
            properties = payload['data']
            all_properties.extend(properties)

            # Fetch next batch of properties if available
            if (i + 1) < total_pages:
                LAND_PAGE = i
                print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
                print("+++++++++++++ Landfield Page Flip to Page " + str(i + 1))
                print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
                payload = await call_landfield(i + 1, batch_size)  # Fetch next batch
                await wait_helper()
                API_CALL_COUNTER = 0  # Reset counter
        except Exception as e:
            print(f"Error fetching or processing landfield data on page {i}: {e}")
            continue  # Continue to the next page if an error occurs

    # Sort all properties by tile count in descending order
    all_properties.sort(key=lambda p: p["attributes"]["tileCount"], reverse=True)

    # Process each property and track new favorites
    for index, property in enumerate(all_properties):
        if index + 1 < start_from_property:
            continue  # Skip properties before the starting point
        if skip_to_next:
            skip_to_next = False  # Reset the flag
            continue  # Move to the next property
        try:
            await favorite_processing(property)
            with open(progress_file, "w") as f:
                f.write(str(index + 2))  # Save the next property to process
        except Exception as e:
            print(f"Error processing property {property['id']}: {e}")
            # Optionally, you can ask here if the user wants to skip to the next property

asyncio.run(main())