import os
import re
import ast
from pathlib import Path
from collections import defaultdict

class DynamoDBAnalyzer:
    def __init__(self, codebase_path):
        self.codebase_path = Path(codebase_path)
        self.results = defaultdict(list)
    
    def find_python_files(self):
        """Find all Python files in the codebase"""
        return list(self.codebase_path.rglob("*.py"))
    
    def extract_env_variable(self, table_instantiation):
        """Extract environment variable name from dynamodb.Table() call"""
        # Handle patterns like:
        # dynamodb.Table(os.environ['TABLE_NAME'])
        # dynamodb.Table(os.environ.get('TABLE_NAME'))
        # dynamodb.Table(table_name) where table_name = os.environ['TABLE_NAME']
        
        env_patterns = [
            r"os\.environ\[(['\"])([^'\"]+)\1\]",  # os.environ['TABLE_NAME']
            r"os\.environ\.get\(['\"]([^'\"]+)['\"]",  # os.environ.get('TABLE_NAME')
            r"os\.getenv\(['\"]([^'\"]+)['\"]",  # os.getenv('TABLE_NAME')
        ]
        
        for pattern in env_patterns:
            match = re.search(pattern, table_instantiation)
            if match:
                # For the first pattern, the table name is in group 2
                # For the second and third patterns, it's in group 1
                if match.lastindex == 2:
                    return match.group(2)
                else:
                    return match.group(1)
        
        return None
    
    def find_variable_assignment(self, lines, line_index, var_name):
        """Find where a variable is assigned, looking backwards from current line"""
        for i in range(line_index - 1, -1, -1):
            line = lines[i].strip()
            # Look for variable assignment patterns with both os.environ formats
            if f"{var_name} =" in line and ("os.environ" in line):
                env_var = self.extract_env_variable(line)
                if env_var:
                    return env_var
                # Also handle the case where it's: var_name = os.environ.get("TABLE_NAME")
                env_get_match = re.search(r'os\.environ\.get\([\'"]([^\'"]+)[\'"]', line)
                if env_get_match:
                    return env_get_match.group(1)
        return None
    
    def extract_function_info(self, lines, line_index):
        """Extract function name and parameters from the function definition"""
        # Look backwards to find the function definition
        func_lines = []
        func_start_line = None
        
        for i in range(line_index, -1, -1):
            line = lines[i].strip()
            if line.startswith("def "):
                func_start_line = i
                func_lines.insert(0, line)
                break
            # If we hit another function or class definition, stop
            elif line.startswith("def ") or line.startswith("class "):
                break
        
        if func_start_line is None:
            return None, []
        
        # Check if this is a multi-line function definition
        # Look forward from the def line to collect the complete function signature
        i = func_start_line
        complete_def = func_lines[0]
        paren_count = complete_def.count('(') - complete_def.count(')')
        
        # If parentheses don't match, it's a multi-line definition
        while paren_count > 0 and i + 1 < len(lines):
            i += 1
            next_line = lines[i].strip()
            complete_def += " " + next_line
            paren_count += next_line.count('(') - next_line.count(')')
        
        # Extract function name and parameters from complete definition
        # Handle cases like: def function_name( param1, param2, ):
        func_match = re.search(r"def\s+(\w+)\s*\((.*?)\):", complete_def, re.DOTALL)
        if func_match:
            func_name = func_match.group(1)
            params_str = func_match.group(2)
            
            # Clean up parameters - remove whitespace and split by comma
            if params_str.strip():
                params = [param.strip().split('=')[0].strip() for param in params_str.split(',') if param.strip()]
                # Remove type hints and default values
                clean_params = []
                for param in params:
                    # Remove type hints (everything after :)
                    if ':' in param:
                        param = param.split(':')[0].strip()
                    clean_params.append(param)
                params = clean_params
            else:
                params = []
            
            return func_name, params
        
        return None, []
    
    def extract_table_operations(self, content, table_var_name, table_line_index, end_line):
        """Extract all table operations within the function scope"""
        lines = content.split('\n')
        operations = []
        
        # Find the function definition
        func_start = None
        for i in range(table_line_index, -1, -1):
            if lines[i].strip().startswith('def '):
                func_start = i
                break
        
        if func_start is None:
            return operations
        
        # Get base indentation
        base_indent = len(lines[func_start]) - len(lines[func_start].lstrip())
        
        # Find function end
        func_end = len(lines)
        for i in range(func_start + 1, len(lines)):
            if lines[i].strip():  # Skip empty lines
                current_indent = len(lines[i]) - len(lines[i].lstrip())
                if current_indent <= base_indent:
                    func_end = i
                    break
        
        # Look for table operations in the function
        in_multiline_call = False
        current_operation = []
        paren_count = 0
        
        for i in range(table_line_index, func_end):
            line = lines[i].strip()
            
            # Case 1: Direct table method calls (existing logic)
            if f'{table_var_name}.' in line:
                # Extract the operation part
                operation_match = re.search(rf'{re.escape(table_var_name)}\.(\w+.*)', line)
                if operation_match:
                    operation_part = operation_match.group(0)
                    
                    # Count parentheses to handle multi-line calls
                    paren_count = operation_part.count('(') - operation_part.count(')')
                    
                    if paren_count == 0:
                        # Single line operation
                        operations.append(operation_part)
                    else:
                        # Start of multi-line operation
                        in_multiline_call = True
                        current_operation = [operation_part]
            
            # Case 2: Table passed as function parameter
            elif table_var_name in line and '(' in line:
                # Check if the table variable is being passed as a parameter
                # Look for patterns like: function_name(table_var, other_params)
                # or variable = function_name(table_var, other_params)
                
                # Find all function calls in the line that include our table variable
                func_call_pattern = r'(\w+)\s*\([^)]*' + re.escape(table_var_name) + r'[^)]*\)'
                func_matches = re.finditer(func_call_pattern, line)
                
                for match in func_matches:
                    # Extract the complete function call
                    func_call_start = match.start()
                    complete_call = self.extract_complete_function_call(line, func_call_start)
                    if complete_call:
                        operations.append(f"Function call: {complete_call}")
            
            # Case 3: Continue collecting multi-line operation (existing logic)
            elif in_multiline_call:
                # Continue collecting multi-line operation
                current_operation.append(line)
                paren_count += line.count('(') - line.count(')')
                
                if paren_count <= 0:
                    # End of multi-line operation
                    operations.append('\n'.join(current_operation))
                    in_multiline_call = False
                    current_operation = []
        
        # Case 4: If no operations found, look for any line that mentions the table variable
        if not operations:
            for i in range(table_line_index, func_end):
                line = lines[i].strip()
                if table_var_name in line and line != lines[table_line_index].strip():
                    # Clean up the line and add it
                    operations.append(f"Referenced in: {line}")
        
        return operations

    def extract_complete_function_call(self, line, start_pos):
        """Extract complete function call from a line"""
        # Find the function name
        func_name_match = re.search(r'(\w+)\s*\(', line[start_pos:])
        if not func_name_match:
            return None
        
        # Find the opening parenthesis
        paren_start = line.find('(', start_pos)
        if paren_start == -1:
            return None
        
        # Count parentheses to find the matching closing one
        paren_count = 0
        i = paren_start
        
        while i < len(line):
            char = line[i]
            if char == '(':
                paren_count += 1
            elif char == ')':
                paren_count -= 1
                if paren_count == 0:
                    # Found the matching closing parenthesis
                    return line[start_pos:i+1]
            i += 1
        
        # If we didn't find a complete call, return what we have
        return line[start_pos:]
    
    def extract_complete_operation(self, text, start_pos):
        """Extract complete operation including multi-line method calls"""
        # Find the start of the operation
        lines = text.split('\n')
        char_count = 0
        start_line_idx = 0
        
        for i, line in enumerate(lines):
            if char_count + len(line) + 1 > start_pos:  # +1 for newline
                start_line_idx = i
                break
            char_count += len(line) + 1
        
        # Extract the operation starting from the identified line
        operation_lines = []
        paren_count = 0
        started = False
        
        for i in range(start_line_idx, len(lines)):
            line = lines[i].strip()
            if not started and '(' in line:
                started = True
            
            if started:
                operation_lines.append(line)
                paren_count += line.count('(') - line.count(')')
                
                if paren_count <= 0:
                    break
        
        return '\n'.join(operation_lines) if operation_lines else text[start_pos:start_pos+100]
    
    def analyze_file(self, file_path):
        """Analyze a single Python file for DynamoDB usage"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            lines = content.split('\n')
            relative_path = str(file_path.relative_to(self.codebase_path))
            
            # Find all dynamodb.Table() instantiations
            for i, line in enumerate(lines):
                if 'dynamodb.Table(' in line:
                    # Extract the saved variable name
                    var_match = re.search(r'(\w+)\s*=\s*.*dynamodb\.Table\(', line)
                    if not var_match:
                        continue
                    
                    saved_var_name = var_match.group(1)
                    
                    # Try to extract env variable directly from the line
                    env_var = self.extract_env_variable(line)
                    
                    # If not found directly, look for variable assignment
                    if not env_var:
                        # Look for patterns like: table_name = os.environ['...']
                        table_arg_match = re.search(r'dynamodb\.Table\(([^)]+)\)', line)
                        if table_arg_match:
                            arg = table_arg_match.group(1).strip()
                            if not ("os.environ" in arg):
                                env_var = self.find_variable_assignment(lines, i, arg)
                    
                    if not env_var:
                        continue
                    
                    # Extract function information
                    func_name, func_params = self.extract_function_info(lines, i)
                    
                    if not func_name:
                        continue
                    
                    # Extract table operations
                    operations = self.extract_table_operations(content, saved_var_name, i, len(lines))
                    
                    # Store the result
                    instance = {
                        'location': relative_path,
                        'function_name': func_name,
                        'saved_variable_name': saved_var_name,
                        'function_variables': func_params,
                        'variable_call_references': operations
                    }
                    
                    self.results[env_var].append(instance)
        
        except Exception as e:
            print(f"Error analyzing file {file_path}: {e}")
    
    def analyze_codebase(self):
        """Analyze the entire codebase"""
        python_files = self.find_python_files()
        
        for file_path in python_files:
            self.analyze_file(file_path)
        
        return self.results
    
    def generate_report(self, output_file='dynamo_analysis.md'):
        """Generate a markdown report of the analysis"""
        with open(output_file, 'w') as f:
            f.write("# DynamoDB Table Usage Analysis\n\n")
            
            for env_var, instances in self.results.items():
                f.write(f"## ENV Variable: {env_var}\n\n")
                
                for idx, instance in enumerate(instances, 1):
                    f.write(f"### Instance {idx}:\n")
                    f.write(f"- **Location (Relative Path)**: {instance['location']}\n")
                    f.write(f"- **Function Name**: {instance['function_name']}\n")
                    f.write(f"- **Saved Variable Name**: {instance['saved_variable_name']}\n")
                    f.write(f"- **Function Variables**: {', '.join(instance['function_variables'])}\n")
                    f.write(f"- **Variable Call References**:\n")
                    
                    for operation in instance['variable_call_references']:
                        # Format the operation for better readability
                        formatted_op = operation.replace('\n', '\n  ')
                        f.write(f"  - `{formatted_op}`\n")
                    
                    f.write("\n")
                
                f.write("---\n\n")

def main():
    # Use current directory as codebase path
    codebase_path = "."
    
    # Initialize analyzer
    analyzer = DynamoDBAnalyzer(codebase_path)
    
    # Analyze codebase
    print("Analyzing codebase for DynamoDB usage...")
    results = analyzer.analyze_codebase()
    
    # Generate report with default filename
    output_file = "dynamo_analysis.md"
    analyzer.generate_report(output_file)
    
    print(f"\nAnalysis complete! Results saved to {output_file}")
    print(f"Found {len(results)} unique DynamoDB tables:")
    
    for env_var, instances in results.items():
        print(f"  - {env_var}: {len(instances)} instance(s)")

if __name__ == "__main__":
    main()