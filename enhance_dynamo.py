import re
import os
from pathlib import Path

class DynamoDBEnhancer:
    def __init__(self, input_file, output_file):
        self.input_file = input_file
        self.output_file = output_file
        self.enhanced_data = []
    
    def analyze_function_purpose_and_fields(self, operations, function_name):
        """Analyze operations to determine purpose and extract key fields"""
        
        purpose_tags = set()
        operation_types = set()
        key_fields = set()
        
        for operation in operations:
            # Determine operation type
            if '.get_item(' in operation or '.query(' in operation or '.scan(' in operation:
                operation_types.add('READ')
            elif '.put_item(' in operation:
                operation_types.add('CREATE')
            elif '.update_item(' in operation:
                operation_types.add('UPDATE')
            elif '.delete_item(' in operation:
                operation_types.add('DELETE')
            elif 'Function call:' in operation:
                operation_types.add('HELPER_FUNCTION')
            elif 'Referenced in:' in operation:
                operation_types.add('REFERENCE')
        
            # Extract key fields from operations
            key_fields.update(self.extract_key_fields(operation))
        
        # Determine purpose based on function name and operations
        purpose_tags.update(self.determine_function_purpose(function_name, operation_types))
        
        return {
            'purpose_tags': list(purpose_tags),
            'operation_types': list(operation_types),
            'key_fields': list(key_fields),
            'data_flow': self.determine_data_flow(operation_types)
        }
    
    def extract_key_fields(self, operation):
        """Extract key field names from DynamoDB operations"""
        fields = set()
        
        # Extract from Key parameters - handles both single and composite keys
        key_matches = re.findall(r'Key=\{[^}]*["\']([^"\']+)["\']:', operation)
        fields.update(key_matches)
        
        # Extract from Item parameters in put_item operations
        item_matches = re.findall(r'Item=\{[^}]*["\']([^"\']+)["\']:', operation)
        fields.update(item_matches)
        
        # Extract from UpdateExpression SET clauses
        update_matches = re.findall(r'SET\s+([^=]+)\s*=', operation)
        for match in update_matches:
            # Handle cases like "SET field1 = :val, field2 = :val2"
            fields_in_set = re.findall(r'(\w+)', match)
            fields.update(fields_in_set)
        
        # Extract from FilterExpression
        filter_matches = re.findall(r'FilterExpression=[^)]*["\']([^"\']+)["\']', operation)
        fields.update(filter_matches)
        
        # Extract field names from ExpressionAttributeNames
        attr_name_matches = re.findall(r'["\']#(\w+)["\']:\s*["\']([^"\']+)["\']', operation)
        for alias, field_name in attr_name_matches:
            fields.add(field_name)
        
        # Remove common non-field words
        non_fields = {'Key', 'Item', 'UpdateExpression', 'ExpressionAttributeValues', 
                     'ExpressionAttributeNames', 'FilterExpression', 'begins_with', 'eq'}
        fields = {f for f in fields if f not in non_fields and len(f) > 1}
        
        return fields
    
    def determine_function_purpose(self, function_name, operation_types):
        """Determine function purpose based on name patterns and operations"""
        purpose_tags = set()
        
        name_patterns = {
            r'create|new|add|insert': 'CREATION',
            r'update|modify|edit|change': 'MODIFICATION', 
            r'delete|remove|destroy': 'DELETION',
            r'get|retrieve|fetch|find|search': 'RETRIEVAL',
            r'list|scan|query': 'LISTING',
            r'auth|permission|access|verify': 'AUTHORIZATION',
            r'validate|check|verify': 'VALIDATION',
            r'bill|cost|usage|track': 'BILLING',
            r'process|handle|execute': 'PROCESSING',
            r'report|generate|export': 'REPORTING',
            r'simulate': 'SIMULATION',
            r'handler': 'EVENT_HANDLER',
            r'assistant': 'AI_ASSISTANT'
        }
        
        for pattern, purpose in name_patterns.items():
            if re.search(pattern, function_name, re.IGNORECASE):
                purpose_tags.add(purpose)
        
        # Add tags based on operation types
        if 'READ' in operation_types and len(operation_types) == 1:
            purpose_tags.add('READ_ONLY')
        elif 'CREATE' in operation_types and 'READ' not in operation_types:
            purpose_tags.add('WRITE_ONLY')
        elif len(set(operation_types) & {'CREATE', 'UPDATE', 'DELETE'}) > 0 and 'READ' in operation_types:
            purpose_tags.add('READ_WRITE')
        elif len(operation_types) > 2:
            purpose_tags.add('COMPLEX_OPERATION')
        
        return purpose_tags
    
    def determine_data_flow(self, operation_types):
        """Determine the data flow pattern"""
        if not operation_types:
            return "UNKNOWN"
        
        has_read = any(op in operation_types for op in ['READ', 'HELPER_FUNCTION'])
        has_write = any(op in operation_types for op in ['CREATE', 'UPDATE', 'DELETE'])
        
        if has_read and has_write:
            return "BIDIRECTIONAL"
        elif has_write:
            return "WRITE"
        elif has_read:
            return "READ"
        else:
            return "REFERENCE_ONLY"
    
    def parse_existing_markdown(self):
        """Parse the existing markdown file to extract data"""
        with open(self.input_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by environment variables
        env_sections = re.split(r'## ENV Variable: ([^\n]+)', content)[1:]  # Skip the header
        
        parsed_data = []
        
        for i in range(0, len(env_sections), 2):
            env_var = env_sections[i].strip()
            section_content = env_sections[i + 1] if i + 1 < len(env_sections) else ""
            
            # Parse instances within each section
            instances = self.parse_instances(section_content)
            
            parsed_data.append({
                'env_var': env_var,
                'instances': instances
            })
        
        return parsed_data
    
    def parse_instances(self, section_content):
        """Parse individual instances from a section"""
        instances = []
        
        # Split by instance headers
        instance_sections = re.split(r'### Instance \d+:', section_content)[1:]
        
        for instance_content in instance_sections:
            instance = {}
            
            # Extract basic information
            location_match = re.search(r'\*\*Location \(Relative Path\)\*\*:\s*([^\n]+)', instance_content)
            function_match = re.search(r'\*\*Function Name\*\*:\s*([^\n]+)', instance_content)
            saved_var_match = re.search(r'\*\*Saved Variable Name\*\*:\s*([^\n]+)', instance_content)
            func_vars_match = re.search(r'\*\*Function Variables\*\*:\s*([^\n]+)', instance_content)
            
            if location_match:
                instance['location'] = location_match.group(1).strip()
            if function_match:
                instance['function_name'] = function_match.group(1).strip()
            if saved_var_match:
                instance['saved_variable_name'] = saved_var_match.group(1).strip()
            if func_vars_match:
                instance['function_variables'] = func_vars_match.group(1).strip()
            
            # Extract variable call references
            references_section = re.search(r'\*\*Variable Call References\*\*:\s*\n(.*?)(?=\n---|\n## |\Z)', 
                                         instance_content, re.DOTALL)
            
            operations = []
            if references_section:
                ref_content = references_section.group(1).strip()
                # Split by lines starting with - or —
                op_lines = re.split(r'\n\s*[-—]\s*', ref_content)
                for line in op_lines:
                    line = line.strip()
                    if line and not line.startswith('**'):
                        # Remove markdown formatting
                        line = re.sub(r'`([^`]+)`', r'\1', line)
                        operations.append(line)
            
            instance['variable_call_references'] = operations
            
            if instance.get('function_name'):  # Only add if we have essential data
                instances.append(instance)
        
        return instances
    
    def enhance_instances(self, parsed_data):
        """Enhance all instances with new analysis"""
        enhanced_data = []
        
        for env_data in parsed_data:
            enhanced_instances = []
            
            for instance in env_data['instances']:
                # Perform analysis
                analysis = self.analyze_function_purpose_and_fields(
                    instance.get('variable_call_references', []),
                    instance.get('function_name', '')
                )
                
                # Merge analysis with existing data
                enhanced_instance = {**instance, **analysis}
                enhanced_instances.append(enhanced_instance)
            
            enhanced_data.append({
                'env_var': env_data['env_var'],
                'instances': enhanced_instances
            })
        
        return enhanced_data
    
    def generate_enhanced_markdown(self, enhanced_data):
        """Generate the enhanced markdown file"""
        content = "# Enhanced DynamoDB Table Usage Analysis\n\n"
        
        for env_data in enhanced_data:
            content += f"## ENV Variable: {env_data['env_var']}\n\n"
            
            for idx, instance in enumerate(env_data['instances'], 1):
                content += f"### Instance {idx}:\n"
                content += f"- **Location (Relative Path)**: {instance.get('location', 'N/A')}\n"
                content += f"- **Function Name**: {instance.get('function_name', 'N/A')}\n"
                content += f"- **Purpose Tags**: {instance.get('purpose_tags', [])}\n"
                content += f"- **Operation Types**: {instance.get('operation_types', [])}\n"
                content += f"- **Key Fields**: {instance.get('key_fields', [])}\n"
                content += f"- **Data Flow**: {instance.get('data_flow', 'UNKNOWN')}\n"
                content += f"- **Saved Variable Name**: {instance.get('saved_variable_name', 'N/A')}\n"
                content += f"- **Function Variables**: {instance.get('function_variables', 'N/A')}\n"
                content += f"- **Variable Call References**:\n"
                
                for operation in instance.get('variable_call_references', []):
                    # Format the operation for better readability
                    formatted_op = operation.replace('\n', '\n  ')
                    content += f"  - `{formatted_op}`\n"
                
                content += "\n"
            
            content += "---\n\n"
        
        return content
    
    def process(self):
        """Main processing function"""
        print("Parsing existing markdown file...")
        parsed_data = self.parse_existing_markdown()
        print(f"Found {len(parsed_data)} environment variables")
        
        print("Enhancing instances with analysis...")
        enhanced_data = self.enhance_instances(parsed_data)
        
        print("Generating enhanced markdown...")
        enhanced_content = self.generate_enhanced_markdown(enhanced_data)
        
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write(enhanced_content)
        
        print(f"Enhanced analysis saved to {self.output_file}")
        
        # Print summary
        total_instances = sum(len(env_data['instances']) for env_data in enhanced_data)
        print(f"Processed {total_instances} total instances")

def main():
    input_file = input("Enter the path to your existing markdown file (default: dynamo_analysis.md): ").strip()
    if not input_file:
        input_file = "dynamo_analysis.md"
    
    output_file = input("Enter the path for the enhanced output file (default: enhanced_dynamo_analysis.md): ").strip()
    if not output_file:
        output_file = "enhanced_dynamo_analysis.md"
    
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' does not exist.")
        return
    
    enhancer = DynamoDBEnhancer(input_file, output_file)
    enhancer.process()

if __name__ == "__main__":
    main()