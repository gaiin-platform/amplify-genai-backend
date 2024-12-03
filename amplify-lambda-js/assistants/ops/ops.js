import Handlebars from "handlebars";


const apiUrl = process.env.API_BASE_URL + '/ops';

const opFormats = {
    "default": `
Operations:
{{#each __assistantOps}}
{{id}}{{#each params}}, "{{description}}"{{/each}} -- {{description}}
{{/each}}
`
}


export const formatOps = async (ops, format) => {
    try {
        const templateStr = format ? opFormats[format] || opFormats["default"] : opFormats["default"];
        const template = Handlebars.compile(templateStr);
        const result = template({"__assistantOps": ops});
        return result;
    } catch (e) {
        console.error(e);
        return "";
    }
}

export const getOps = async (accessToken, tag) => {

    const response = await fetch(apiUrl +"/get", {
        method: "POST",
        body: JSON.stringify(
            {
                data:{
                    tag
                }
            }),
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer "+accessToken
        },
    });

    if (!response.ok) {
        console.error("Error fetching ops: ", response.statusText);
        return [];
    }

    const data = await response.json();

    if(!data || !data.data) {
        console.error("Missing data in ops response: ", response.statusText);
        return [];
    }

    return data.data;
}