#from dotenv import load_dotenv
import yaml
import os
#add to python file that needs vars
#For local testing
#from ..var.localvar import load_yaml_as_env
#yaml_file_path = "C:\\Users\\karnsab\Desktop\\amplify-lambda-mono-repo\\var\local-var.yml"
#load_yaml_as_env(yaml_file_path)


# Function to convert YAML content to .env format and load it
def load_yaml_as_env(yaml_path):
    with open(yaml_path, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    # Convert YAML dictionary to .env format (KEY=VALUE)
    for key, value in data_loaded.items():
        os.environ[key] = str(value)
