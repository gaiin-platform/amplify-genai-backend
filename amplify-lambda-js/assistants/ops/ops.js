import Handlebars from "handlebars";


const apiUrl = process.env.API_BASE_URL + '/ops';

const opFormats = {
    "default": `
Operations:
{{#each __assistantOps}}
{{{id}}}: {{{description}}}
{{#if parameters}}
{{#if parameters.properties}}
{{#each parameters.properties}}
  {{{@key}}}: {{{description}}}{{#if type}} ({{type}}){{/if}}
{{/each}}
{{else}}
{{#each parameters}}
  {{{name}}}: {{{description}}}{{#if type}} ({{type}}){{/if}}
{{/each}}
{{/if}}
{{/if}}
{{/each}}
`,
    "urlFormat": `
Operations:
{{#each __assistantOps}}
{{{url}}}: {{{description}}}
{{#if parameters}}
{{#if parameters.properties}}
{{#each parameters.properties}}
  {{{@key}}}: {{{description}}}{{#if type}} ({{type}}){{/if}}
{{/each}}
{{else}}
{{#each parameters}}
  {{{name}}}: {{{description}}}{{#if type}} ({{type}}){{/if}}
{{/each}}
{{/if}}
{{/if}}
{{/each}}
`,
    "integrationsFormat": `
    Integrations:
{{#each __assistantOps}}

PATH: {{{url}}}
Tags: {{joinFiltered tags}}


{{/each}}
`,
}

Handlebars.registerHelper('joinFiltered', function(tags, options) {
    const filtered = tags.filter(t => t !== 'all' && t !== 'default');
    return filtered.join(', ');
});
// NoEscape - Handlebars will not convert HTML or special characters into HTML entities when displaying your template variables
export const formatOps = async (ops, format, noEscape = false) => {
    
    try {
        const templateStr = format ? opFormats[format] || opFormats["default"] : opFormats["default"];
        const template = Handlebars.compile(templateStr, { noEscape: noEscape });
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