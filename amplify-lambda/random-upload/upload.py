import requests
import os

def upload_file():
    try:
        # current_user = "amp-4b475f60-f4b2-4500-b497-4ff6ce1d6714"
        name = "API_documentation.docx"
        file_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        # tags = []
        # props = {}
        # knowledge_base = 'default'

        file_path = "/Users/kyleshepherd/Downloads/amplify-lambda-mono/amplify-lambda-api/service/API_Document.docx"

        with open(file_path, 'rb') as file:
            file_content = file.read()

        # print(f"Current user: {current_user}")
        # print(f"File name: {name}")
        # print(f"File type: {file_type}")

        upload_url = "https://vu-amplify-dev-rag-input.s3.amazonaws.com/Amplify_System_Assistants/2024-07-17/78854d49-109c-4411-ab86-98fa5b3531e3.json?AWSAccessKeyId=ASIAXPRBCDVUXHZ4EZ6L&Signature=xH9nAC3CyGGibgxmBlddSkche5U%3D&content-type=application%2Fvnd.openxmlformats-officedocument.wordprocessingml.document&x-amz-security-token=IQoJb3JpZ2luX2VjECkaCXVzLWVhc3QtMSJHMEUCIQCedyhwPOUk155MshmKY%2BZINZhOLu8S9Pl%2BGS6NvlDRxQIgNWDoy0dSfAdSS0tsWoXe9ZUawsl7L%2BAVZIS8f%2FS6LV8qgAQI8v%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTQzOTE2NzgzMTMiDNhKH5XSaMFB7kIXgCrUA%2FtvCqZIoi9Q1PJIZi%2FT3Gsnu6rZ5nr01CevbCNVS63jsUtZ7p%2FayP8QEq%2B0tmEPvzpZlBACHSD2OT%2Fd6anxPO8Zfb%2B6g5Ndpg4fWU1s4PDpfVvsV%2BQjrfMMwmKiUoi%2B823Or0Y5ZIloiad5JKbEw6mf7hToJ6Y2%2B3IpUMabaNNO2aC3bEt8yF85expFUFdPchqQc0ylPyRfIPT%2BHyePvK6sKnO0%2FyVPgoQeSE5Hj5PsBn4C%2BdUbDgXX%2BIjeA9gXvyxN7Y3Wg0VLxrTbISgqp%2FonymLPtcMRdM5fOuKcgckig7WhASwj1OGXsa08yhRR5b7iZ1J2PiPK8YmESGW2nSUyY0Xlewn4f8YHWgrnrR1oSLkYZEI7sx3gHXaq23px1IvNAgMlnprS0hhxRL%2Bi7QsvEYlFyx4ce0y4OKODdB8lgNMjXmgSJ1UglPyb1Lg4ZxltDceyhneMr7bve%2BXtfLZ8y3cIR0iUaKwPuYlcmx3cyuxOIe7PMvkRnyroTrByQBDfwQ2pnA4RW8b21Y3v%2BHKRQ1I%2FbUi%2BCEe5sSETYUB%2BifB8YtgBOLB88VbZT3%2BLYPsSe53W4MbauIK4Tz%2F6e6jly%2F%2BflIbiDTE97%2F3K0hiv9D8n4jDK6d%2B0BjqmAeerynA23d2v%2FAJN6SnX1d%2BTgzm3n4GhLK9qzhV%2BqzecNZVmeOsTIKgQcAcn6GqdhvHVa8unfAq%2Bg3%2FhFXag5x5cyuAYKLeHcHaWAd4BzMiuEAGKmt1qvkSiZecuMWqVR8fo1HfxY9mSrsAg6j9UiUB2CoEBqrvRFpIlcOhA7WeXSj4l3FFL8R%2Blv8HcAh0Yg5a5aYIt7bMNdRbkGYCqjzFUlm1hpQ4%3D&Expires=1721247153"
        response = requests.put(upload_url, file_content, headers={'Content-Type': file_type})
        print(response)
        print(f"Upload response status code: {response.status_code}")
        
        if response.status_code == 200:
            print("File uploaded successfully")
        else:
            print(f"Failed to upload file. Status code: {response.status_code}")
            return None

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return None
    

def main():
    result = upload_file()
    if result:
        print("File upload completed successfully.")
    else:
        print("File upload failed.")

if __name__ == "__main__":
    main()