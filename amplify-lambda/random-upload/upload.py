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

        file_path = "/Users/kyleshepherd/Downloads/amplify-lambda-mono/amplify-assistants/assistants/system_assistants/API_documentation.docx"

        with open(file_path, 'rb') as file:
            file_content = file.read()

        # print(f"Current user: {current_user}")
        # print(f"File name: {name}")
        # print(f"File type: {file_type}")

        upload_url = "https://vu-amplify-dev-rag-input.s3.amazonaws.com/Amplify_System_Assistants/2024-07-16/e89d17e3-3abe-4e55-bfc5-0a5000c476f9.json?AWSAccessKeyId=ASIAXPRBCDVU2X3YEHXJ&Signature=9D%2FzFBfF%2BeLYe9g5anItKXayqkc%3D&content-type=application%2Fvnd.openxmlformats-officedocument.wordprocessingml.document&x-amz-security-token=IQoJb3JpZ2luX2VjEBgaCXVzLWVhc3QtMSJHMEUCIQDxCaIAvilsOZDiiHXCfjTtd2QfpJbszWV0TdW6E7ea8wIgBNHapSUcExbPdZYX0LhqtnanPuGoCddxkhgiV3tW1sQqgAQI4f%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw1MTQzOTE2NzgzMTMiDB6xCkHiHAq0WG7oXCrUAypBxaGAq56l3K4DMIX6PNolTeFlG9C8QXwvegGtV91syMC9HIyp619dYV%2BUo15Rx7kBP%2FuhtCSNeTMOIIAZH0KY6cuuMH5L%2B3tJwUjAF8GNRV3mjM7cX5IG9m%2FfnV6qW7y4XltdHrybLVa9VSdSxuYrrGGO7eA7UZ%2BMN9MFtdGa%2BVaOo14TF2bTjOoDJdQTXyW7cuqnWEF0PgLrSWM4Eh1W94sm%2FoqMAgonRFqSZ9qWdjzuBN9HE4Yxzrcyyz%2FsT1JrtUPFAuob%2FfNeCph8rcroE4cUaJDWR%2BnAR7Cx0Ni04UVUpZWOPqbPOROfWq7cQXE2%2F%2BYZ4XNMbAOVJyWY06w4XcY%2FAWCQWuOc73p%2F9lr5JbzazE9rczXmhn3fixd0C%2Bbpp%2FbhswbR%2Ff%2BolGDOu4m4PSM4NBxWlGYGNw0MjcTF87eEWdZ07GNSsiaMPSoU5op%2BL2g6A9YKapBgpVn8WIorSVDYC%2Bi0j3gVg%2FnVXafaTH5AdnJCt5VD4BJBQenMBKNU3CPAPgCyI6U%2Bzw7OCWtkMWJvpl%2FZ3e7mqAgrgVxvn1AsX9Bn%2BYvJwqOz77MNqE52s6sxSR1xg%2BjodO3BpQqdKO%2Fp51UvfUH%2FMwnp%2FGLPOURDwjCJgty0BjqmAUpXZIGHHma9lQh4xuCIjPgmZsj7UtTvXh85ub73awqtm5dnJPTwxUx3f39OBdfwX51Dl6D5ygunIBIZFMHKuFG0GF2gGS0o9fp1L1mqmfHjthyyA9cvAPbUTiwuuhAnTRE8QlLF7Mnpj4GDN2o2zFpOvK1%2Bi5t7t4Vvq1mt1BE89%2B6Apv1%2Blm54ylkpzVcet8uVJuIeoRmgCGBBNZ9uzG3orPRQ3vg%3D&Expires=1721176506"
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