'''
Lambda script to receive Twilio message, parse YELP API call, determine best option to
respond with and respond to.
'''

import os
import json
import logging
import datetime
from difflib import SequenceMatcher

import boto3
from botocore.vendored import requests
from botocore.exceptions import ClientError

YELP_API_KEY = os.getenv("YELP_API_KEY")

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def parse_message(event):
    '''
    Seperate incoming message into JSON object for further usage.
    '''
    zip_code = event['FromZip']
    city = event['FromCity'].replace("+", " ")
    state = event['FromState'].replace("+", " ")
    body = event['Body'].replace("+", " ")

    message = {
        "term": body,
        "location": f"{city} {state} {zip_code}"
    }

    return message


def pull_s3(client):
    '''
    Call S3 bucket containing database of previously requested businesses and their info
    '''
    database = {}

    try:
        reply = client.list_objects(
            Bucket=os.environ['S3_Bucket_Name']
        )

        in_bucket = False
        if 'Contents' in reply:
            contents = reply['Contents']

            for key in contents:
                if f"{os.environ['S3_Object_Name']}.json" in key:
                    in_bucket = True
                    break

    except ClientError as error:
        LOGGER.error('unable to list S3 bucket\n%s', error, exc_info=True)

    try:
        if in_bucket:
            response = client.get_object(
                Bucket=os.environ['S3_Bucket_Name'],
                Key=f"{os.environ['S3_Object_Name']}.json"
            )['Body'].read()

            database["businesses"] = response

        else:
            database["businesses"] = {}

    except ClientError as error:
        LOGGER.error('unable to pull from S3\n%s', error, exc_info=True)

    return database


def parse_yelp(data):
    '''
    Parse YELP API call for the information we are tracking.
    '''
    date = datetime.date.today().strftime("%Y%m%d")
    businesses = []

    for business in data['businesses']:
        add = {
            'dateCreated': date,
            'dateUpdated': date,
            'name': business['name'],
            'review_count': business['review_count'],
            'rating': business['rating'],
            'location': business['location'],
            'phone': business['phone'],
            'num_visited': 0,
            'categories': business['categories']
        }

        businesses.append(add)

    return businesses


def yelp_call(term, location):
    '''
    Make a YELP GET request.
    '''
    headers = {"Authorization": "Bearer %s" % YELP_API_KEY}
    url = "https://api.yelp.com/v3/businesses/search"
    params = {"term": term, "location": location}

    req = requests.get(url, params=params, headers=headers)

    if (req.status_code < 300) and (req.status_code > 199):
        data = json.loads(req.text)
    else:
        LOGGER.error("Status Code not in the 200s")

    return parse_yelp(data)


def check_business(database, msg):
    '''
    If database missing, create one.
    '''
    term = msg['term']
    location = msg['location']

    if not database['businesses']:
        database['businesses'] = {}

    if term not in database['businesses']:
        data = yelp_call(term, location)
        database["businesses"][term] = data

    database['dateUpdated'] = datetime.date.today().strftime("%Y%m%d")

    return database


def structure_twilio_message(choice):
    '''
    Format message for Twilio to read.
    '''
    name = choice['name']
    rating = choice['rating']
    location = choice['location']['address1'] + \
        ' ' + choice['location']['city']
    phone = choice['phone']

    return '<?xml version=\"1.0\" encoding=\"UTF-8\"?>'\
          f'<Response><Message>Name: {name}\nRating: {rating}\n'\
          f'Location: {location}\nPhone: {phone}</Message></Response>'


def update_list(database, best_choice):
    '''
    Update the business selected with one more visit.
    '''
    for b_type, businesses in database['businesses'].items():
        for i, _ in enumerate(businesses):
            if businesses[i]['name'] == best_choice['name']:
                database['businesses'][b_type][i]['num_visited'] += 1
                return database

    return database


def similar(term, name):
    '''
    Determine from 0.0 to 1.0 how much of the string matches.
    '''
    return SequenceMatcher(None, term, name).ratio()


def check_dates(business, date):
    '''
    Determine if business has been visited in the last week or two and rank
    '''
    b_date = datetime.datetime.strptime(business['dateUpdated'], '%Y%m%d')

    delta = str(b_date - date)[0]

    if 7 < int(delta) < 15:
        return -10
    if 0 < int(delta) < 8:
        return -5

    return 0


def best_option(database, message):
    '''
    Rank business and determine best option to respond to Twilio with.
    '''
    term = message['term']
    city = message['location'].split()[0]

    date = datetime.datetime.strptime(datetime.date.today().strftime("%Y%m%d"), '%Y%m%d')

    best_choice = ''
    best_score = 0

    for b_type, businesses in database["businesses"].items():
        for business in businesses:
            score = 0

            if b_type == term:
                score += 2
            if business['location']['city'] == city:
                score += 1

            for cat in business['categories']:
                score += similar(term, cat['alias']) * 3

            score += similar(term, business['name']) * 10

            score += check_dates(business, date)

            score += business['num_visited'] * -5

            if score > best_score:
                best_score = score
                best_choice = business

    businesses = update_list(database, best_choice)
    twilio_msg = structure_twilio_message(best_choice)

    return businesses, twilio_msg


def push_s3(client, businesses):
    '''
    Put business database into S3 bucket.
    '''
    try:
        client.put_object(
            Bucket=os.environ['S3_Bucket_Name'],
            Key=f"{os.environ['S3_Object_Name']}.json",
            Body=json.dumps(businesses)
        )

    except ClientError as error:
        LOGGER.error("error pushing to S3\n%s", error, exc_info=True)


def handler(event, _):
    '''
    Entry point for the Lambda script.
    '''
    client = boto3.client("s3")

    print("Parsing incoming text...")
    msg = parse_message(event)

    print("Pulling from S3 bucket...")
    temp_db = pull_s3(client)

    print("Checking if type from message is in bucket...")
    database = check_business(temp_db, msg)

    print("Ranking and selecting business...")
    businesses, message = best_option(database, msg)

    print("Updating and pushing to S3...")
    push_s3(client, businesses)

    print("Structuring and replying to sender...")
    return message
