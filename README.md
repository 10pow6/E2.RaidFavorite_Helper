Open access for all.

# What is this:
This script runs through your properties with droids and then favorites all properties in range that have 0 civs/0 droids attached to them. It skips properties that are already favorited.

# Why is this useful:
Quality of life improvement for initial raid target list.

# What does this need:
Seems to be working. Use at own risk.

# How To Use:
1) Python 3.13
2) Create a folder and save below items
3) Create venv in that folder (via command prompt)
`python -m venv venv`
4) Activate venv
`venv\Scripts\activate`
5) Install requirements
`pip install -r requirements.txt`
6) Rename your config.sample.ini to config.ini
7) Grab your cookie string & xcsrf string from E2 Web
8) Drop in the value into your config.ini
9) main.py
10) On your config, you'll want to set your TILE_BREAKER_COUNT to the cut off number (anything below that tile count will not be favorited)