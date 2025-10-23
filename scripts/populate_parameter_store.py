#!/usr/bin/env python3
"""
Script to populate AWS Parameter Store with environment variables from serverless.yml files.
Parses all serverless.yml files in the monorepo and extracts "Locally Defined Variables".
"""

import sys
import yaml
import boto3
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import re

# Add CloudFormation intrinsic function support to YAML loader
class CloudFormationLoader(yaml.SafeLoader):
    pass

def construct_ref(loader, node):
    return {'Ref': loader.construct_scalar(node)}

def construct_getatt(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return {'Fn::GetAtt': loader.construct_scalar(node)}
    elif isinstance(node, yaml.SequenceNode):
        return {'Fn::GetAtt': loader.construct_sequence(node)}
    else:
        # Handle any other node type gracefully
        return {'Fn::GetAtt': str(node.value) if hasattr(node, 'value') else str(node)}

def construct_sub(loader, node):
    return {'Fn::Sub': loader.construct_scalar(node)}

def construct_join(loader, node):
    return {'Fn::Join': loader.construct_sequence(node)}

def construct_import_value(loader, node):
    return {'Fn::ImportValue': loader.construct_mapping(node)}

# Register CloudFormation intrinsic functions
CloudFormationLoader.add_constructor('!Ref', construct_ref)
CloudFormationLoader.add_constructor('!GetAtt', construct_getatt)
CloudFormationLoader.add_constructor('!Sub', construct_sub)
CloudFormationLoader.add_constructor('!Join', construct_join)
CloudFormationLoader.add_constructor('!ImportValue', construct_import_value)

class ParameterStorePopulator:
    def __init__(self, stage: str, dep_name: str, region: str = 'us-east-1', dry_run: bool = False):
        self.stage = stage
        self.dep_name = dep_name
        self.region = region
        self.dry_run = dry_run
        if not dry_run:
            self.ssm_client = boto3.client('ssm', region_name=region)
        else:
            self.ssm_client = None
        self.processed_services = []
        
        # Load and populate shared variables first
        self.shared_variables = self.load_shared_variables()
        
    def find_serverless_files(self, root_dir: str) -> List[Path]:
        """Find all serverless.yml files in the monorepo."""
        serverless_files = []
        root_path = Path(root_dir)
        
        for item in root_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                serverless_file = item / 'serverless.yml'
                if serverless_file.exists():
                    serverless_files.append(serverless_file)
                    print(f"Found serverless.yml in: {item.name}")
                else:
                    print(f"Skipping {item.name}: No serverless.yml found")
        
        return serverless_files

    def parse_serverless_yml(self, file_path: Path) -> Tuple[Optional[str], Dict[str, str]]:
        """Parse serverless.yml and extract service name and locally defined variables."""
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            # Parse YAML with CloudFormation support
            data = yaml.load(content, Loader=CloudFormationLoader)
            
            # Extract service name and resolve DEP_NAME
            service_template = data.get('service', '')
            service_name = self.resolve_service_name(service_template)
            
            # Find locally defined variables
            locally_defined_vars = self.extract_locally_defined_vars(content)
            
            # Resolve variables
            resolved_vars = self.resolve_variables(locally_defined_vars, service_name)
            
            return service_name, resolved_vars
            
        except Exception as e:
            print(f"YAML parsing failed for {file_path}: {e}")
            print("Attempting fallback regex parsing...")
            
            # Fallback: Try to extract just what we need using regex
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Extract service name using regex
                service_match = re.search(r'service:\s*(.+)', content)
                if service_match:
                    service_template = service_match.group(1).strip()
                    service_name = self.resolve_service_name(service_template)
                    
                    # Find locally defined variables
                    locally_defined_vars = self.extract_locally_defined_vars(content)
                    
                    # Resolve variables
                    resolved_vars = self.resolve_variables(locally_defined_vars, service_name)
                    
                    print(f"Fallback parsing successful for {service_name}")
                    return service_name, resolved_vars
                else:
                    print(f"Could not extract service name from {file_path}")
                    return None, {}
                    
            except Exception as fallback_error:
                print(f"Fallback parsing also failed for {file_path}: {fallback_error}")
                return None, {}

    def resolve_service_name(self, service_template: str) -> str:
        """Resolve service name by substituting DEP_NAME."""
        # Replace both old and new DEP_NAME formats with actual dep_name
        resolved = service_template.replace('${self:custom.stageVars.DEP_NAME}', self.dep_name)
        resolved = resolved.replace('${self:custom.depName}', self.dep_name)
        return resolved

    def extract_locally_defined_vars(self, content: str) -> Dict[str, str]:
        """Extract variables from the 'Locally Defined Variables' section."""
        lines = content.split('\n')
        locally_defined_vars = {}
        in_locally_defined = False
        in_imported = False
        
        for line in lines:
            stripped = line.strip()
            
            # Check for section markers
            if '# Locally Defined Variables' in line:
                in_locally_defined = True
                in_imported = False
                continue
            elif '# Imported Variables from Parameter Store' in line or '# Imported Variables' in line:
                in_locally_defined = False
                in_imported = True
                continue
            elif stripped.startswith('#') and ('Variables' in stripped or stripped == '#'):
                in_locally_defined = False
                in_imported = False
                continue
            
            # Only process lines in the "Locally Defined Variables" section
            if in_locally_defined and ':' in line and not line.strip().startswith('#'):
                # Parse environment variable line
                if line.startswith('    ') or line.startswith('\t'):
                    var_line = line.strip()
                    if ':' in var_line:
                        var_name = var_line.split(':')[0].strip()
                        var_value = ':'.join(var_line.split(':')[1:]).strip()
                        
                        # Strip YAML comments (anything after #)
                        if '#' in var_value:
                            var_value = var_value.split('#')[0].strip()
                        
                        locally_defined_vars[var_name] = var_value
        
        return locally_defined_vars

    def resolve_variables(self, variables: Dict[str, str], service_name: str) -> Dict[str, str]:
        """Resolve serverless variables in the values."""
        resolved = {}
        
        for var_name, var_value in variables.items():
            # Handle boolean values properly
            if var_value.lower() in ['true', 'false']:
                resolved[var_name] = var_value.lower()
                continue
                
            # Replace common serverless variables
            resolved_value = var_value
            resolved_value = resolved_value.replace('${self:service}', service_name)
            resolved_value = resolved_value.replace('${sls:stage}', self.stage)
            resolved_value = resolved_value.replace('${self:provider.stage}', self.stage)
            resolved_value = resolved_value.replace('${self:custom.stageVars.DEP_NAME}', self.dep_name)
            resolved_value = resolved_value.replace('${self:custom.depName}', self.dep_name)
            
            resolved[var_name] = resolved_value
            
        return resolved

    def load_shared_variables(self) -> Dict[str, str]:
        """Load shared stageVars from var file and populate Parameter Store."""
        var_file = Path(f"var/{self.stage}-var.yml")
        
        print(f"\nProcessing shared variables from {var_file}")
        print("=" * 50)
        
        if not var_file.exists():
            print(f"Warning: {var_file} not found, using DEP_NAME only")
            shared_vars = {'DEP_NAME': self.dep_name}
        else:
            try:
                with open(var_file, 'r') as f:
                    var_data = yaml.safe_load(f)
                
                # Define the shared variables to migrate
                shared_var_names = [
                    'ADMINS', 'CHANGE_SET_BOOLEAN', 'CUSTOM_API_DOMAIN', 'DEP_REGION', 'IDP_PREFIX',
                    'LOG_LEVEL', 'OAUTH_AUDIENCE', 'OAUTH_ISSUER_BASE_URL', 'PANDOC_LAMBDA_LAYER_ARN',
                    'ASSISTANTS_OPENAI_PROVIDER', 'LLM_ENDPOINTS_SECRETS_NAME_ARN',
                    'AGENT_ENDPOINT', 'BEDROCK_GUARDRAIL_ID', 'BEDROCK_GUARDRAIL_VERSION',
                    'COGNITO_CLIENT_ID', 'COGNITO_USER_POOL_ID', 'ORGANIZATION_EMAIL_DOMAIN',
                    'API_VERSION', 'MAX_ACU', 'MIN_ACU', 'PRIVATE_SUBNET_ONE',
                    'PRIVATE_SUBNET_TWO', 'VPC_CIDR', 'VPC_ID'
                ]
                
                shared_vars = {}
                
                # Add hardcoded shared values (stage-based naming patterns)
                stage = self.stage
                shared_vars['LLM_ENDPOINTS_SECRETS_NAME'] = f"{stage}-openai-endpoints"
                shared_vars['SECRETS_ARN_NAME'] = f"{stage}-amplify-app-secrets"
                shared_vars['APP_ARN_NAME'] = f"{stage}-amplify-app-vars"
                
                # Add DEP_NAME from terminal argument
                shared_vars['DEP_NAME'] = self.dep_name
                
                # Add other variables from var file
                for var_name in shared_var_names:
                    if var_name in var_data:
                        value = var_data[var_name]
                        # Handle boolean values properly for serverless
                        if isinstance(value, bool):
                            shared_vars[var_name] = str(value).lower()  # Convert True/False to true/false
                        else:
                            shared_vars[var_name] = str(value)
                
            except Exception as e:
                print(f"Error loading shared variables from {var_file}: {e}")
                shared_vars = {'DEP_NAME': self.dep_name}
        
        # Create Parameter Store entries for shared variables
        print(f"Creating {len(shared_vars)} shared parameters:")
        success_count = 0
        for var_name, var_value in shared_vars.items():
            print(f"  {var_name}: {var_value}")
            if self.create_shared_parameter(var_name, var_value):
                success_count += 1
        
        print(f"Successfully created {success_count}/{len(shared_vars)} shared parameters\n")
        return shared_vars

    def create_shared_parameter(self, var_name: str, var_value: str) -> bool:
        """Create a shared parameter in AWS Parameter Store."""
        parameter_name = f"/amplify/{self.stage}/{var_name}"
        
        if self.dry_run:
            print(f"[DRY RUN] Would create shared parameter: {parameter_name} = {var_value}")
            return True
        
        try:
            # Check if parameter already exists
            try:
                existing = self.ssm_client.get_parameter(Name=parameter_name)
                
                # Update if different
                if existing['Parameter']['Value'] != var_value:
                    self.ssm_client.put_parameter(
                        Name=parameter_name,
                        Value=var_value,
                        Type='String',
                        Overwrite=True,
                        Description=f"Shared variable used across all services"
                    )
                    print(f"    Updated: {parameter_name} = {var_value}")
                else:
                    print(f"    No change: {parameter_name}")
                    
            except self.ssm_client.exceptions.ParameterNotFound:
                # Create new parameter
                self.ssm_client.put_parameter(
                    Name=parameter_name,
                    Value=var_value,
                    Type='String',
                    Description=f"Shared variable used across all services"
                )
                print(f"    Created: {parameter_name} = {var_value}")
                
            return True
            
        except Exception as e:
            print(f"    Error: {parameter_name}: {e}")
            return False

    def create_parameter_store_entry(self, service_name: str, var_name: str, var_value: str) -> bool:
        """Create a parameter in AWS Parameter Store."""
        # Create parameter path: /amplify/{stage}/{service_name}/{var_name}
        parameter_name = f"/amplify/{self.stage}/{service_name}/{var_name}"
        
        if self.dry_run:
            print(f"[DRY RUN] Would create parameter: {parameter_name} = {var_value}")
            return True
        
        try:
            # Check if parameter already exists
            try:
                existing = self.ssm_client.get_parameter(Name=parameter_name)
                print(f"Parameter {parameter_name} already exists with value: {existing['Parameter']['Value']}")
                
                # Update if different
                if existing['Parameter']['Value'] != var_value:
                    self.ssm_client.put_parameter(
                        Name=parameter_name,
                        Value=var_value,
                        Type='String',
                        Overwrite=True,
                        Description=f"Locally defined variable from {service_name} service"
                    )
                    print(f"Updated parameter: {parameter_name} = {var_value}")
                else:
                    print(f"No change needed for: {parameter_name}")
                    
            except self.ssm_client.exceptions.ParameterNotFound:
                # Create new parameter
                self.ssm_client.put_parameter(
                    Name=parameter_name,
                    Value=var_value,
                    Type='String',
                    Description=f"Locally defined variable from {service_name} service"
                )
                print(f"Created parameter: {parameter_name} = {var_value}")
                
            return True
            
        except Exception as e:
            print(f"Error creating parameter {parameter_name}: {e}")
            return False

    def process_service(self, serverless_file: Path) -> bool:
        """Process a single service's serverless.yml file."""
        print(f"\nProcessing: {serverless_file.parent.name}")
        print("=" * 50)
        
        service_name, variables = self.parse_serverless_yml(serverless_file)
        
        if not service_name:
            print(f"Could not determine service name for {serverless_file}")
            return False
            
        if not variables:
            print(f"No locally defined variables found in {serverless_file}")
            return True
            
        print(f"Service: {service_name}")
        print(f"Found {len(variables)} locally defined variables:")
        
        success_count = 0
        for var_name, var_value in variables.items():
            print(f"  {var_name}: {var_value}")
            if self.create_parameter_store_entry(service_name, var_name, var_value):
                success_count += 1
                
        print(f"Successfully processed {success_count}/{len(variables)} parameters")
        
        self.processed_services.append({
            'service': service_name,
            'file': str(serverless_file),
            'variables': len(variables),
            'success': success_count
        })
        
        return success_count == len(variables)

    def run(self, root_dir: str = '.'):
        """Main execution method."""
        print(f"Populating Parameter Store for stage: {self.stage}, dep_name: {self.dep_name}")
        print(f"Region: {self.region}")
        print("=" * 80)
        
        serverless_files = self.find_serverless_files(root_dir)
        
        if not serverless_files:
            print("No serverless.yml files found!")
            return False
            
        print(f"\nFound {len(serverless_files)} serverless.yml files\n")
        
        total_success = 0
        for serverless_file in serverless_files:
            if self.process_service(serverless_file):
                total_success += 1
                
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total services processed: {len(serverless_files)}")
        print(f"Successful: {total_success}")
        print(f"Failed: {len(serverless_files) - total_success}")
        
        if self.processed_services:
            print("\nDetails:")
            for service_info in self.processed_services:
                status = "✓" if service_info['success'] == service_info['variables'] else "✗"
                print(f"  {status} {service_info['service']}: {service_info['success']}/{service_info['variables']} parameters")
        
        return total_success == len(serverless_files)


def main():
    parser = argparse.ArgumentParser(description='Populate AWS Parameter Store with serverless.yml variables')
    parser.add_argument('--stage', required=True, help='Deployment stage (dev, staging, prod)')
    parser.add_argument('--dep-name', required=True, help='Deployment name for DEP_NAME variable')
    parser.add_argument('--region', default='us-east-1', help='AWS region (default: us-east-1)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    
    args = parser.parse_args()
    
    if args.dry_run:
        print("DRY RUN MODE - No parameters will be created")
    
    try:
        populator = ParameterStorePopulator(args.stage, args.dep_name, args.region, args.dry_run)
        success = populator.run()
        
        if success:
            print("\n✓ All services processed successfully!")
            sys.exit(0)
        else:
            print("\n✗ Some services failed to process")
            sys.exit(1)
            
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()