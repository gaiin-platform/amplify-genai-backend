
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import json
import sys
import glob

import os
import json
import boto3

# Set up the S3 client
s3_client = boto3.client('s3')

def update_index_json_files(dir_path, s3_bucket):
    for root, dirs, files in os.walk(dir_path):
        print(f"Working on directory: {root}")
        index_file = os.path.join(root, 'index.json')

        if os.path.isfile(index_file):
            with open (index_file, 'r') as json_file:
                data = json.load(json_file)
                items = []

                json_files = glob.glob(root + '/*.json')
                for file in json_files:
                    if file == index_file:
                        continue
                    with open(file, 'r') as individual_file:
                        item_data = json.load(individual_file)
                        item = {k: item_data.get(k, None) for k in ('id', 'name', 'description', 'createdAt',
                                                                    'category', 'user', 'tags', 'updatedAt')}
                        items.append(item)

                data['items'] = items

            # Save updated file
            with open(index_file, 'w') as json_file:
                json.dump(data, json_file)

            s3_key = data["id"] + '/index.json'

            if s3_key == '//index.json':
                s3_key = 'index.json'

            #confirm = input(f"Do you want to upload {index_file} to {s3_bucket} {s3_key}? (yes/no): ")
            #if confirm.lower() == 'yes':
                # Upload updated file to S3
            s3_client.upload_file(index_file, s3_bucket, s3_key)
            print(f"Uploaded {index_file} to {s3_bucket}/{s3_key}")
            #else:
            #    print(f"Skipped uploading {index_file}")

if __name__ == "__main__":
    dir_path = sys.argv[1]
    s3_bucket = sys.argv[2]
    update_index_json_files(dir_path, s3_bucket)