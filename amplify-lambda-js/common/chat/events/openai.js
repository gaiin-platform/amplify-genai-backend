//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas



export const transform = (event) => {
    if (event && event.choices && event.choices.length > 0) {
        if(event.choices[0].delta && event.choices[0].delta.tool_calls){
            const calls = event.choices[0].delta.tool_calls;
            return {d: {tool_calls:calls}};
        }
        else if(event.choices[0].delta && event.choices[0].delta.content) {
            return {d: event.choices[0].delta.content};
        } else if (event.choices[0].message && event.choices[0].message.content) {
            return {d: event.choices[0].message.content};
        } else if (event && event.d && event.d.delta && event.d.delta.text) { // for error message
            return {d: event.d.delta.text}
        }
    } 
    console.log("----NO MATCH---", event , "\n\n")
    return null;
    
}