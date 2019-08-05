#!/bin/bash


# Fill out the following variables
apikey=             # Your Yelp API key
appname=            # Name of the app
bucket=             # Name of your bucket
region=             # Name of your region of your bucket

# Change these variables if you'd like
object=seekstore    # Name of the key in the bucket which stores Yelp data
lambda=lambda.zip   # Name of the Lambda zip file to be used

aws s3api create-bucket --bucket $bucket --region $region --create-bucket-configuration LocationConstraint=$region

zip $lambda lambda.py

aws s3api put-object --bucket $bucket --key $lambda --body $lambda

aws cloudformation package --template-file template.yml --output-template-file template-out.yml --s3-bucket $bucket

aws cloudformation create-stack --stack-name $appname --template-body file://template-out.yml --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --parameters ParameterKey=AppName,ParameterValue=$appname ParameterKey=S3BucketName,ParameterValue=$bucket ParameterKey=S3ObjectName,ParameterValue=$object ParameterKey=LambdaName,ParameterValue=$lambda ParameterKey=YelpApiKey,ParameterValue=$apikey ParameterKey=RegionName,ParameterValue=$region