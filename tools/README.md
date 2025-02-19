# Ops Management Script

This script provides functionality for managing operations (ops) in a Python project. It can scan Python files for decorated functions, extract operation details, and register them in a DynamoDB table.

## Features

- Scan Python files for operations decorated with `@op` or `@vop`
- Extract operation details including path, method, description, and parameters
- Register operations in a DynamoDB table
- List operations found in the project

## Prerequisites

- Python 3.7+
- boto3
- pydantic
- PyYAML

## Installation

1. Clone the repository
2. Install the required packages:

```
pip install boto3 pydantic PyYAML
```

## Usage

The script can be run with the following commands:

### List Operations

To list all operations found in the project:

```
python script_name.py ls [--dir DIRECTORY]
```

- `--dir`: Optional. Specify the directory to search for ops (default is current directory)

### Register Operations

To register operations in the DynamoDB table:

```
python script_name.py register [--stage STAGE] [--dir DIRECTORY] [--ops_table TABLE_NAME]
```

- `--stage`: Optional. Specify the staging environment
- `--dir`: Optional. Specify the directory to search for ops (default is current directory)
- `--ops_table`: Optional. Specify the DynamoDB table name

## Configuration

The script uses the following configuration options:

- `OPS_DYNAMODB_TABLE`: The name of the DynamoDB table to store operations
    - Can be set as an environment variable
    - Can be specified in a `var/<stage>-var.yml` file
    - Can be passed as a command-line argument with `--ops_table`

## Project Structure

- `find_python_files()`: Finds all Python files in the specified directory
- `extract_ops_from_file()`: Extracts operation details from a Python file
- `scan_ops()`: Scans all Python files and extracts operations
- `write_ops()`: Writes operations to the DynamoDB table
- `resolve_ops_table()`: Resolves the DynamoDB table name from various sources

## Models

The script uses Pydantic models to validate operation data:

- `ParamModel`: Represents a parameter of an operation
- `OperationModel`: Represents an operation with its details

## Note

Ensure you have the necessary AWS credentials configured to access DynamoDB.