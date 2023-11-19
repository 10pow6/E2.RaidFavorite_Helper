import configparser
import asyncio
import httpx
import json
import random

config=configparser.ConfigParser()
config.read('config.ini')

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

async def call_landfield(page):
    global API_CALL_COUNTER
    API_CALL_COUNTER+=1
    
    api_call="/droids/landfields?page="+str(page)+"&q=&sortDir=asc"

    @api_with_backoff
    async def call_api():    
        async with httpx.AsyncClient(base_url=R_API) as client:
            r = await client.get(api_call, headers=headers)
        return r
    
    r = await call_api()

    return r.json()

async def call_get_attack_targets(landfield,page,droids_url_str):
    global API_CALL_COUNTER
    API_CALL_COUNTER+=1

    api_call="/droids/landfields/" + landfield + "/" + "raid?autos=false&favorites=false&page=" + str(page) + "&q=&recents=false&sortBy=tilesCount&sortDir=desc&" + droids_url_str

    @api_with_backoff
    async def call_api():
        async with httpx.AsyncClient(base_url=R_API) as client:
            r = await client.get(api_call, headers=headers) 
        return r
      
    r = await call_api()

    return r.json()

async def call_favorite(at_landfield):
    global API_CALL_COUNTER
    API_CALL_COUNTER+=1

    api_call="/landfields/" + at_landfield + "/toggle_favorite"

    @api_with_backoff
    async def call_api():
        async with httpx.AsyncClient(base_url=R_API) as client:
            r = await client.put(api_call, headers=headers,data=None)    
        return r
    
    r = await call_api()

    return r.json()


async def favorite_processing( property ):   
    property_id=property["id"]
    print( "<< Processing " + str(property_id) + " >>")
    droid_ids=property['meta']['droidIds']
    if( len(droid_ids) > 0  ):
        droids_url_str="withinRangeDroidIds[]="+"&withinRangeDroidIds[]=".join(droid_ids)
    else:
        print("\t<< No droids >>")
        return #nothing to process, no droids
    
    payload=await call_get_attack_targets(property_id,1,droids_url_str)
    await wait_helper()
    total_at_pages=payload["meta"]["pages"]

    for i in range(1,total_at_pages+1):
    #for i in range(1,2): #testing mode only; replace with above line to iterate through all pages
        targets = payload['data']
        for target in targets:
            at_droids=target['meta']['numberOfDroids']
            at_civs=target['meta']['numberOfCivilians']
            at_favorited=target['meta']['favorite']
            at_landfield_id=target["id"]
            at_owner_id=target["attributes"]["ownerId"]
            at_description=target["attributes"]["description"]
            at_tile_tier=target["attributes"]["tileClass"]
            at_tile_count=target["attributes"]["tileCount"]

            processing_str = "\t<< Processing A.T. " + str(at_landfield_id) + ": "
            processing_str += "[ TIER: "+str(at_tile_tier)
            processing_str += " | TILES: "+str(at_tile_count) 
            processing_str += " | CIVS: " + str(at_civs) 
            processing_str += " | DROIDS: " + str(at_droids) 
            processing_str += " | FAVORITE: " + str(at_favorited) + "]"
            print( processing_str )
            # break out of loop, we reached our breaker
            if( at_tile_count < TILE_BREAKER_COUNT ):
                print("\t\t<< Breaker threshold reached >>")
                return

            # favorite the property if 0 droids and 0 civs (and not favorited currently)
            if( at_droids == 0 and at_civs == 0 and at_favorited==None ):
                favorite_payload=await call_favorite(at_landfield_id)
                print("\t\t" + "|| Favorite Added || " + str(favorite_payload))
                await wait_helper(level=2)

        if( i + 1 ) < total_at_pages:
            print( "\t++ A.T. Page Flip to Page " + str(i+1) + " ++")
            payload = await call_get_attack_targets(property_id,i+1,droids_url_str)
            await wait_helper(level=1)


async def main():
    global LAND_PAGE
    # get first page details
    payload = await call_landfield(1)
    await wait_helper()
    total_pages=payload['meta']['pages']
    print("<< Total Raid Property Pages: " + str(total_pages) + " >>")
    
    for i in range(1,total_pages+1):
    #for i in range(79,80): #testing mode only; replace with above line to iterate through all pages
        properties = payload['data']

        for property in properties:
            await favorite_processing(property)
        
        if( i + 1 ) <= total_pages:
            LAND_PAGE = i
            print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
            print("+++++++++++++ Landfield Page Flip to Page " + str(i+1))
            print("++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
            payload = await call_landfield(i+1)
            await wait_helper()
            API_CALL_COUNTER=0 #reset to 0 as we are at top level

asyncio.run(main())