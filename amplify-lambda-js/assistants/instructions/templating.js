import Handlebars from "handlebars";
import yaml from "js-yaml";
import {formatOps, getOps} from "../ops/ops.js";


function parseAllOpsOccurrences(templateStr) {
    const pattern = /\{\{\s*ops\s+([a-zA-Z0-9_./\-]+)(?::([a-zA-Z0-9_./\-]+))?(\s+noAdd)?\s*\}\}/g;

    let match;
    const results = [];
    while ((match = pattern.exec(templateStr)) !== null) {
      const raw = match[0];
      const tag = match[1].trim();
      const format = match[2];
      const noAdd = !!match[3]; // True if we matched " noAdd"
      const id = format ? `${tag}:${format}` : tag;
      results.push({ id, tag, format, noAdd });
    }
    return results;
  }

export const fillInTemplate = async (llm, params, body, ds, templateStr, contextData) => {

    contextData = {
        ...contextData,
        user: params.account.user,
    }

    let result = templateStr;
    try {
        const ASSISTANT_OPS_STR = "__assistantOps";

        let includedOperations = contextData.operations || [];

        const opsOccurrences = parseAllOpsOccurrences(templateStr);
        let hasTemplateForOps = opsOccurrences.length > 0 || templateStr.includes(ASSISTANT_OPS_STR);
        const opsByTag = {};
        const opsFormatMap = {};

        for (const { id, tag, format, noAdd } of opsOccurrences) {
            if (!opsByTag[tag]) {
                const fetchedOps = await getOps(params.account.accessToken, tag);  
                opsByTag[tag] = fetchedOps;
                opsFormatMap[id] = await formatOps(fetchedOps, format, noAdd)
                if (!noAdd) includedOperations = [...includedOperations, ...fetchedOps];
            }
        }

        if ( includedOperations.length > 0) { 
            llm.sendStateEventToStream({ resolvedOps: includedOperations });
            contextData[ASSISTANT_OPS_STR] = includedOperations;
            
            if (!hasTemplateForOps) {
                templateStr = `{{ ops ${ASSISTANT_OPS_STR} }}\n\n` + templateStr;
                opsFormatMap[ASSISTANT_OPS_STR] = await formatOps(contextData.operations);
            }
        }
        const isQuotedRegex = /^(['"]).*\1$/;
        Object.keys(opsFormatMap).forEach(op => {
            if (!(isQuotedRegex.test(op))) {
                templateStr = templateStr.replaceAll(`ops ${op}`, `ops "${op}"`);
            }
        })
        Handlebars.registerHelper("ops", function (opKey) {
            if (!opKey || !opsFormatMap[opKey]) return "";
            // console.log("opsFormatMap[opKey]: ", opsFormatMap[opKey]);
            return opsFormatMap[opKey];
          });
   

        const dataSourcesInConversationAlready = (body) => {
            return body.messages.slice(0, -1)
                .map(m => m.data)
                .filter(d => d != null)
                .map(d => d.dataSources)
                .filter(d => d != null)
                .flat();
        }

        Handlebars.registerHelper('dataSources', function (tagandformat) {
            const all = [...dataSourcesInConversationAlready(body), ...ds];
            return yaml.dump(all);
        });

        Handlebars.registerHelper('dataSourcesInConversation', function (tagandformat) {
            const mds = dataSourcesInConversationAlready(body);
            return yaml.dump(mds);
        });

        Handlebars.registerHelper('dataSourcesInCurrentMessage', function (tagandformat) {

            return yaml.dump(ds);
        });

        Handlebars.registerHelper('assistantName', function () {
            return contextData.assistant.name;
        });

        Handlebars.registerHelper('user', function () {
            return contextData.user;
        });

        Handlebars.registerHelper('datetime', function (fmt) {
            // Output a date string in the provided fmt
            return new Date().toISOString();
        });

        Handlebars.registerHelper('yaml', function (context) {
            return yaml.dump(context);
        });

        Handlebars.registerHelper('API_BASE_URL', function () {
            return process.env.API_BASE_URL;
        });

        const template = Handlebars.compile(templateStr);
        result = template(contextData);

    } catch (e) {
        console.error(e);
    }

    return result;
}

