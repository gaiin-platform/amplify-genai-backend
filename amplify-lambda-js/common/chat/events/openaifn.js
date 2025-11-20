//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

export const transform = (event) => {

    /* This is what the events look like:
    undefined:{"tool_calls":[{"index":0,"id":"call_XYD3ADLkrp5ELZy2bK6yRi9R","type":"function","function":{"name":"answer","arguments":""}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":"{\n"}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":" "}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":" \""}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":"value"}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":"\":"}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":" true"}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":"\n"}}]}
undefined:{"tool_calls":[{"index":0,"function":{"arguments":"}"}}]}
     */
    event = JSON.parse(event);
    let delta = "";
    if (event.tool_calls) {
        for (const call of event.tool_calls) {
            if (call.function && call.function.name) {
                delta += "{\"call\":\"" + call.function.name + "\",\"arguments\":";
            }
            if (call.function && call.function.arguments) {
                delta += call.function.arguments;
            }
        }
    }
    return delta;
}