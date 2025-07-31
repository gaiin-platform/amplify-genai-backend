import boto3
import os
import re
from botocore.exceptions import NoCredentialsError
from pathlib import Path

def get_all_dynamodb_tables(region_name='us-east-1'):
    session = boto3.Session(profile_name='default') # Replace with your profile
    client = session.client('dynamodb', region_name='us-east-1') # Replace with your region

    table_names = []
    paginator = client.get_paginator('list_tables')
    for page in paginator.paginate():
        table_names.extend(page['TableNames'])

    return table_names

def convert_table_names(table_names):
    """Convert table names to the three different patterns"""
    first_check = []
    second_check = []
    third_check = []
    
    for table_name in table_names:
        # Check if table follows the expected pattern
        if table_name.startswith('amplify-'):
            parts = table_name.split('-')
            if len(parts) >= 5:  # amplify-v12-foo-dev-bar minimum
                # Find the 'dev' part (or similar stage)
                stage_index = -1
                for i, part in enumerate(parts):
                    if part in ['dev', 'prod', 'staging', 'test']:
                        stage_index = i
                        break
                
                if stage_index > 2:  # Ensure we have enough parts before stage
                    # Pattern 1: amplify-{self:custom.stageVars.DEP_NAME}-foo-${opt:stage, 'dev'}-bar
                    pattern1_parts = parts.copy()
                    pattern1_parts[1] = '{self:custom.stageVars.DEP_NAME}'
                    pattern1_parts[stage_index] = "${opt:stage, 'dev'}"
                    first_check.append('-'.join(pattern1_parts))
                    
                    # Pattern 2: ${self:service}-${sls:stage}-bar
                    # Extract everything before stage as service, everything after as suffix
                    service_parts = parts[:stage_index]
                    suffix_parts = parts[stage_index + 1:]
                    pattern2 = '${self:service}-${sls:stage}'
                    if suffix_parts:
                        pattern2 += '-' + '-'.join(suffix_parts)
                    second_check.append(pattern2)
                    
                    # Pattern 3: amplify-${self:custom.stageVars.DEP_NAME}-foo-${sls:stage}-bar
                    pattern3_parts = parts.copy()
                    pattern3_parts[1] = '${self:custom.stageVars.DEP_NAME}'
                    pattern3_parts[stage_index] = '${sls:stage}'
                    third_check.append('-'.join(pattern3_parts))
                else:
                    # If no standard stage found, create patterns anyway
                    first_check.append(table_name)
                    second_check.append(table_name)
                    third_check.append(table_name)
            else:
                # If not enough parts, add as-is
                first_check.append(table_name)
                second_check.append(table_name)
                third_check.append(table_name)
        else:
            # If doesn't start with amplify-, add as-is
            first_check.append(table_name)
            second_check.append(table_name)
            third_check.append(table_name)
    
    return first_check, second_check, third_check

def search_codebase_for_patterns(patterns, search_directory='.'):
    """Search the entire codebase for pattern occurrences"""
    results = {}
    
    # File extensions to search
    extensions = ['.js', '.ts', '.json', '.yml', '.yaml', '.py', '.txt']
    
    for pattern in patterns:
        results[pattern] = {'found': False, 'files': []}
        
        # Walk through all files in the directory
        for root, dirs, files in os.walk(search_directory):
            # Skip common directories that shouldn't be searched
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', 'env']]
            
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()
                
                if file_ext in extensions:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            if pattern in content:
                                results[pattern]['found'] = True
                                results[pattern]['files'].append(file_path)
                    except Exception as e:
                        # Skip files that can't be read
                        continue
    
    return results

def generate_markdown_report(table_names, first_check, second_check, third_check, 
                           results1, results2, results3, output_file='dynamodb_table_analysis_2.md'):
    """Generate a markdown report with the analysis results"""
    
    with open(output_file, 'w') as f:
        f.write("# DynamoDB Table Pattern Analysis Report\n\n")
        f.write(f"**Total tables analyzed:** {len(table_names)}\n\n")
        
        f.write("## Summary\n\n")
        
        # Count results for each pattern
        pattern1_found = sum(1 for pattern in first_check if results1[pattern]['found'])
        pattern2_found = sum(1 for pattern in second_check if results2[pattern]['found'])
        pattern3_found = sum(1 for pattern in third_check if results3[pattern]['found'])
        
        f.write(f"- **Pattern 1** (`{{self:custom.stageVars.DEP_NAME}}` + `${{opt:stage, 'dev'}}`): {pattern1_found}/{len(first_check)} found\n")
        f.write(f"- **Pattern 2** (`${{self:service}}` + `${{sls:stage}}`): {pattern2_found}/{len(second_check)} found\n")
        f.write(f"- **Pattern 3** (`${{self:custom.stageVars.DEP_NAME}}` + `${{sls:stage}}`): {pattern3_found}/{len(third_check)} found\n\n")
        
        f.write("## Detailed Results\n\n")
        
        for i, table_name in enumerate(table_names):
            f.write(f"### Table: `{table_name}`\n\n")
            
            # Pattern 1
            pattern1 = first_check[i]
            found1 = results1[pattern1]['found']
            f.write(f"**Pattern 1:** `{pattern1}`\n")
            f.write(f"- **Found:** {'✅ Yes' if found1 else '❌ No'}\n")
            if found1 and results1[pattern1]['files']:
                f.write(f"- **Files:** {', '.join(results1[pattern1]['files'])}\n")
            f.write("\n")
            
            # Pattern 2
            pattern2 = second_check[i]
            found2 = results2[pattern2]['found']
            f.write(f"**Pattern 2:** `{pattern2}`\n")
            f.write(f"- **Found:** {'✅ Yes' if found2 else '❌ No'}\n")
            if found2 and results2[pattern2]['files']:
                f.write(f"- **Files:** {', '.join(results2[pattern2]['files'])}\n")
            f.write("\n")
            
            # Pattern 3
            pattern3 = third_check[i]
            found3 = results3[pattern3]['found']
            f.write(f"**Pattern 3:** `{pattern3}`\n")
            f.write(f"- **Found:** {'✅ Yes' if found3 else '❌ No'}\n")
            if found3 and results3[pattern3]['files']:
                f.write(f"- **Files:** {', '.join(results3[pattern3]['files'])}\n")
            f.write("\n")
            
            f.write("---\n\n")

if __name__ == "__main__":
    region = 'us-east-1'  # Change if using a different AWS region
    
    print("🔍 Getting DynamoDB tables...")
    tables = get_all_dynamodb_tables(region)
    print(f"✅ Found {len(tables)} DynamoDB tables in region '{region}'")
    
    print("\n🔄 Converting table names to patterns...")
    first_check, second_check, third_check = convert_table_names(tables)
    
    print("🔍 Searching codebase for Pattern 1...")
    results1 = search_codebase_for_patterns(first_check)
    
    print("🔍 Searching codebase for Pattern 2...")
    results2 = search_codebase_for_patterns(second_check)
    
    print("🔍 Searching codebase for Pattern 3...")
    results3 = search_codebase_for_patterns(third_check)
    
    print("📝 Generating markdown report...")
    generate_markdown_report(tables, first_check, second_check, third_check, 
                           results1, results2, results3)
    
    print("✅ Analysis complete! Check 'dynamodb_table_analysis.md' for detailed results.")
    
    # Print quick summary
    pattern1_found = sum(1 for pattern in first_check if results1[pattern]['found'])
    pattern2_found = sum(1 for pattern in second_check if results2[pattern]['found'])
    pattern3_found = sum(1 for pattern in third_check if results3[pattern]['found'])
    
    print(f"\n📊 Quick Summary:")
    print(f"   Pattern 1: {pattern1_found}/{len(first_check)} found")
    print(f"   Pattern 2: {pattern2_found}/{len(second_check)} found")
    print(f"   Pattern 3: {pattern3_found}/{len(third_check)} found")
