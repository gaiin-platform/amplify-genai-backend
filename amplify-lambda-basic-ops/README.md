# Amplify Basic Ops for Assistants

This is a set of operations that are common across assistants, such as prompting. 

## HTTP Request Paths
| **HTTP Path** | **Method** | **Handler**                      | **Purpose**                                                                                      |
|---------------|------------|---------------------------------|--------------------------------------------------------------------------------------------------|
| `/llm/query`  | POST       | `service/core.llm_prompt_datasource` | Endpoint to prompt the LLM that can be used by an Assistant or turned into a custom data source. |