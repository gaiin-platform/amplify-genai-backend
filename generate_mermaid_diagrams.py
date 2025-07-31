import re
import os
from collections import defaultdict, Counter

class MermaidDiagramGenerator:
    def __init__(self, enhanced_file, output_dir="mermaid_diagrams"):
        self.enhanced_file = enhanced_file
        self.output_dir = output_dir
        self.color_map = {
            'CREATION': '#90EE90',      # Light Green
            'RETRIEVAL': '#87CEEB',     # Sky Blue
            'MODIFICATION': '#FFD700',   # Gold
            'DELETION': '#FF6347',      # Tomato
            'AUTHORIZATION': '#DDA0DD', # Plum
            'BILLING': '#F0E68C',       # Khaki
            'PROCESSING': '#D2B48C',    # Tan
            'REPORTING': '#98FB98',     # Pale Green
            'VALIDATION': '#FFA07A',    # Light Salmon
            'LISTING': '#B0C4DE',       # Light Steel Blue
            'READ_ONLY': '#E0E0E0',     # Light Gray
            'WRITE_ONLY': '#FFB6C1',    # Light Pink
            'READ_WRITE': '#FFEFD5',    # Papaya Whip
            'COMPLEX_OPERATION': '#DCDCDC', # Gainsboro
            'AI_ASSISTANT': '#E6E6FA',  # Lavender
            'EVENT_HANDLER': '#F5DEB3', # Wheat
            'SIMULATION': '#AFEEEE'     # Pale Turquoise
        }
    
    def parse_enhanced_markdown(self):
        """Parse the enhanced markdown file"""
        with open(self.enhanced_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by environment variables
        env_sections = re.split(r'## ENV Variable: ([^\n]+)', content)[1:]
        
        parsed_data = []
        
        for i in range(0, len(env_sections), 2):
            env_var = env_sections[i].strip()
            section_content = env_sections[i + 1] if i + 1 < len(env_sections) else ""
            
            instances = self.parse_enhanced_instances(section_content)
            
            if instances:  # Only add if we have instances
                parsed_data.append({
                    'env_var': env_var,
                    'instances': instances
                })
        
        return parsed_data
    
    def parse_enhanced_instances(self, section_content):
        """Parse enhanced instances from a section"""
        instances = []
        instance_sections = re.split(r'### Instance \d+:', section_content)[1:]
        
        for instance_content in instance_sections:
            instance = {}
            
            # Extract all the enhanced fields
            patterns = {
                'location': r'\*\*Location \(Relative Path\)\*\*:\s*([^\n]+)',
                'function_name': r'\*\*Function Name\*\*:\s*([^\n]+)',
                'purpose_tags': r'\*\*Purpose Tags\*\*:\s*(\[.*?\])',
                'operation_types': r'\*\*Operation Types\*\*:\s*(\[.*?\])',
                'key_fields': r'\*\*Key Fields\*\*:\s*(\[.*?\])',
                'data_flow': r'\*\*Data Flow\*\*:\s*([^\n]+)',
                'saved_variable_name': r'\*\*Saved Variable Name\*\*:\s*([^\n]+)',
                'function_variables': r'\*\*Function Variables\*\*:\s*([^\n]+)'
            }
            
            for field, pattern in patterns.items():
                match = re.search(pattern, instance_content)
                if match:
                    value = match.group(1).strip()
                    if field in ['purpose_tags', 'operation_types', 'key_fields']:
                        # Parse list format
                        value = self.parse_list_field(value)
                    instance[field] = value
            
            # Extract operations
            references_section = re.search(r'\*\*Variable Call References\*\*:\s*\n(.*?)(?=\n---|\n## |\Z)', 
                                         instance_content, re.DOTALL)
            operations = []
            if references_section:
                ref_content = references_section.group(1).strip()
                op_lines = re.split(r'\n\s*[-—]\s*', ref_content)
                for line in op_lines:
                    line = line.strip()
                    if line and not line.startswith('**'):
                        line = re.sub(r'`([^`]+)`', r'\1', line)
                        operations.append(line)
            
            instance['variable_call_references'] = operations
            
            if instance.get('function_name'):
                instances.append(instance)
        
        return instances
    
    def parse_list_field(self, list_str):
        """Parse string representation of list back to actual list"""
        try:
            # Remove brackets and split by comma, then clean up quotes
            list_str = list_str.strip('[]')
            if not list_str:
                return []
            items = [item.strip().strip("'\"") for item in list_str.split(',')]
            return [item for item in items if item]
        except:
            return []
    
    def generate_table_diagram(self, env_var, instances):
        """Generate mermaid diagram for a specific table"""
        
        # Clean table name for use as filename
        clean_table_name = re.sub(r'[^a-zA-Z0-9_]', '_', env_var)
        
        diagram = f"""---
title: {env_var} - DynamoDB Table Interactions
---
graph TD
    DB[("{env_var}<br/>DynamoDB Table")]
    DB:::database
    
"""
        
        # Group functions by service (based on file path)
        services = defaultdict(list)
        for instance in instances:
            location = instance.get('location', '')
            service = location.split('/')[0] if '/' in location else 'root'
            services[service].append(instance)
        
        # Generate nodes for each service
        service_colors = ['#FFE4E1', '#E0FFFF', '#F0FFF0', '#FFF8DC', '#F5F5DC', '#FFE4B5']
        color_idx = 0
        
        for service, service_instances in services.items():
            diagram += f"    subgraph {service.replace('-', '_')}[\"📁 {service}\"]\n"
            
            for instance in service_instances:
                func_name = instance.get('function_name', 'unknown')
                func_id = f"{service}_{func_name}".replace('-', '_').replace('.', '_')
                
                # Get primary purpose for color
                purposes = instance.get('purpose_tags', [])
                operations = instance.get('operation_types', [])
                key_fields = instance.get('key_fields', [])
                data_flow = instance.get('data_flow', 'UNKNOWN')
                
                # Create function node label
                purpose_str = ', '.join(purposes[:2]) if purposes else 'GENERAL'
                operation_str = ', '.join(operations) if operations else 'N/A'
                fields_str = ', '.join(key_fields[:3]) if key_fields else 'N/A'
                
                label = f"{func_name}\\n{purpose_str}\\n⚡ {operation_str}\\n🔑 {fields_str}"
                
                diagram += f"        {func_id}[\"{label}\"]\n"
                
                # Add styling based on primary purpose
                primary_purpose = purposes[0] if purposes else 'GENERAL'
                if primary_purpose in self.color_map:
                    diagram += f"        {func_id}:::{primary_purpose.lower()}\n"
            
            diagram += f"    end\n"
            diagram += f"    {service.replace('-', '_')}:::service{color_idx % len(service_colors)}\n\n"
            color_idx += 1
        
        # Add connections based on data flow
        for service, service_instances in services.items():
            for instance in service_instances:
                func_name = instance.get('function_name', 'unknown')
                func_id = f"{service}_{func_name}".replace('-', '_').replace('.', '_')
                data_flow = instance.get('data_flow', 'UNKNOWN')
                operations = instance.get('operation_types', [])
                
                # Add connections based on operations
                if 'READ' in operations or 'HELPER_FUNCTION' in operations:
                    diagram += f"    DB -.-> {func_id}\n"
                if any(op in operations for op in ['CREATE', 'UPDATE', 'DELETE']):
                    diagram += f"    {func_id} --> DB\n"
        
        # Add styling definitions
        diagram += """
    %% Styling
    classDef database fill:#4CAF50,stroke:#2E7D32,stroke-width:3px,color:#fff
    classDef creation fill:#90EE90,stroke:#228B22,stroke-width:2px
    classDef retrieval fill:#87CEEB,stroke:#4682B4,stroke-width:2px
    classDef modification fill:#FFD700,stroke:#DAA520,stroke-width:2px
    classDef deletion fill:#FF6347,stroke:#DC143C,stroke-width:2px
    classDef authorization fill:#DDA0DD,stroke:#9370DB,stroke-width:2px
    classDef billing fill:#F0E68C,stroke:#BDB76B,stroke-width:2px
    classDef processing fill:#D2B48C,stroke:#A0522D,stroke-width:2px
    classDef reporting fill:#98FB98,stroke:#32CD32,stroke-width:2px
    classDef validation fill:#FFA07A,stroke:#FF4500,stroke-width:2px
    classDef listing fill:#B0C4DE,stroke:#4682B4,stroke-width:2px
    classDef read_only fill:#E0E0E0,stroke:#696969,stroke-width:2px
    classDef write_only fill:#FFB6C1,stroke:#FF1493,stroke-width:2px
    classDef read_write fill:#FFEFD5,stroke:#DEB887,stroke-width:2px
    classDef complex_operation fill:#DCDCDC,stroke:#A9A9A9,stroke-width:2px
    classDef ai_assistant fill:#E6E6FA,stroke:#9370DB,stroke-width:2px
    classDef event_handler fill:#F5DEB3,stroke:#D2B48C,stroke-width:2px
    classDef simulation fill:#AFEEEE,stroke:#5F9EA0,stroke-width:2px
"""
        
        # Add service styling
        for i in range(len(service_colors)):
            diagram += f"    classDef service{i} fill:{service_colors[i]},stroke:#999,stroke-width:1px\n"
        
        return diagram
    
    def generate_summary_diagram(self, all_data):
        """Generate a high-level summary diagram showing all tables"""
        diagram = """---
title: DynamoDB Tables Overview
---
graph TD
    subgraph legend["Legend"]
        L1["Read Operations"]
        L2["Write Operations"] 
        L3["Read+Write Operations"]
    end
    
"""
        
        # Add nodes for each table
        for table_data in all_data:
            env_var = table_data['env_var']
            instances = table_data['instances']
            
            # Count operation types
            read_funcs = sum(1 for i in instances if 'READ' in i.get('operation_types', []))
            write_funcs = sum(1 for i in instances if any(op in i.get('operation_types', []) 
                            for op in ['CREATE', 'UPDATE', 'DELETE']))
            
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', env_var)
            
            # Create table node
            diagram += f"    {clean_name}[\"{env_var}\\n📊 {len(instances)} functions\\n🔍 {read_funcs} reads, ✏️ {write_funcs} writes\"]\n"
            diagram += f"    {clean_name}:::table\n\n"
        
        diagram += """
    %% Styling
    classDef table fill:#4CAF50,stroke:#2E7D32,stroke-width:2px,color:#fff
    classDef legend fill:#f9f9f9,stroke:#ccc,stroke-width:1px
"""
        
        return diagram
    
    def create_output_directory(self):
        """Create output directory if it doesn't exist"""
        os.makedirs(self.output_dir, exist_ok=True)
    
    def generate_all_diagrams(self):
        """Generate all diagrams"""
        print("Parsing enhanced markdown file...")
        all_data = self.parse_enhanced_markdown()
        
        self.create_output_directory()
        
        print(f"Generating diagrams for {len(all_data)} tables...")
        
        # Generate individual table diagrams
        for table_data in all_data:
            env_var = table_data['env_var']
            instances = table_data['instances']
            
            print(f"  Generating diagram for {env_var}...")
            diagram = self.generate_table_diagram(env_var, instances)
            
            # Save diagram
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', env_var)
            filename = f"{clean_name.lower()}_diagram.mmd"
            filepath = os.path.join(self.output_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(diagram)
            
            print(f"    Saved: {filepath}")
        
        # Generate summary diagram
        print("Generating summary diagram...")
        summary_diagram = self.generate_summary_diagram(all_data)
        summary_path = os.path.join(self.output_dir, "tables_overview.mmd")
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(summary_diagram)
        
        print(f"Summary diagram saved: {summary_path}")
        print(f"\n✅ Generated {len(all_data)} table diagrams + 1 summary diagram")
        print(f"All files saved in: {self.output_dir}")

def main():
    input_file = input("Enter the path to your enhanced markdown file (default: enhanced_dynamo_analysis.md): ").strip()
    if not input_file:
        input_file = "enhanced_dynamo_analysis.md"
    
    output_dir = input("Enter the output directory for mermaid files (default: mermaid_diagrams): ").strip()
    if not output_dir:
        output_dir = "mermaid_diagrams"
    
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' does not exist.")
        return
    
    generator = MermaidDiagramGenerator(input_file, output_dir)
    generator.generate_all_diagrams()
    
    print("\n Diagram generation complete!")
    print("You can now:")
    print("1. Copy the .mmd file contents into mermaid.live to view")
    print("2. Use mermaid-cli to generate images: mmdc -i diagram.mmd -o diagram.png")
    print("3. Include them in markdown files with ```mermaid blocks")

if __name__ == "__main__":
    main()