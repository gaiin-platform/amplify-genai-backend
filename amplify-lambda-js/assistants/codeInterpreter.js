import {
    AssistantState, chainActions,
    DoneState, outputToStatus, invokeAction, 
    PromptAction, StateBasedAssistant, updateStatus
} from "./statemachine/states.js";
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import {deleteRequestState} from "../requests/requestState.js";
import {trace} from "../common/trace.js";




const logger = getLogger("codeInterpreterAssitant");
const description = "Only to be used when user specifically asks to use code interpreter or the user asks to create / generate png, pdf, or csv files."
// `This assistants  executes Python in a secure sandbox, handling diverse data to craft files and visual graphs.
// It tackles complex code and math challenges through iterative problem-solving, refining failed attempts into successful executions.
// Use this for complex mathmatical operations and coding tasks that involve the need to run the code in a sandbox environment.
// Only to be used when user specifically asks to use code interpreter or the user asks to create / generate png, pdf, or csv files.`;

const additionalPrompt = `You have access to a sandboxed environment for writing and testing code when code is requested:
                            1. Anytime you write new code display a preview of the code to show your work.
                            2. Run the code to confirm that it runs.
                            3. Show us the input data and output of your code in Markdown
                            Always display code blocks in markdown. comment the code with explanations of what the code is doing for any complex sections of code. 
                            Do not include download links or mock download links! Instead tell me some information about the files you have produced and refer to them by their corresponding file name. For any created files, draw connections between the users prompt and how it relates to your generated files. Always attach and send back generated files.` 


const requestErrorResponse = (context, message) => {
    sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse( message || { success: false, error: 'Internal error when making code interpreter request.'} ))

}

const cleanupRequest = async (context) => {
    const user  = context.params.account.user
    const requestId = context.body.options.requestId || user;
    await deleteRequestState(user, requestId);

    const doTrace = process.env.TRACING_ENABLED;
    if(doTrace) {
        trace(requestId, ["response"], {stream: context.responseStream.trace})
    }  
}

//function call to code_interpeter endpoints in an action type format
const invokeCodeIterpreterAction = 
    async (llm, context, dataSources) => { 
        if (!(context)) return; 
        const account = context.params.account;
        const token = account.accessToken;
        const options = context.body.options;
        const messages = context.body.messages;

        // if we have a codeInterpreterId then we can chat if we dont then we have to create first 
        // The conversation currently does have an assistantID in our database (which contains an assistantID with code_interpreter)
        let assistantId = options.codeInterpreterAssistantId || null;

        // The conversation currently does not have an assistantID in our database 
        if (assistantId === null) {
            
            const create_data = {
                access_token: token,
                name: "CodeInterpreter",
                description: description, 
                tags: [],
                instructions:  options.prompt + additionalPrompt,
                dataSources: [], // unless we make this userdefined assistant compatible then the assistant wont have any data sources. any ds in messages will be added to the openai thread 
            }

            try {
                const responseData = await fetchRequest(token, create_data, process.env.ASSISTANTS_API_BASE_URL + '/assistant/create/codeinterpreter'); 

                if (responseData && responseData && responseData.success) {
                    assistantId = responseData.data.assistantId
                    //we need to ensure we send the assistant_id back to be saved in the conversation
                    sendDeltaToStream(context.responseStream, "codeInterpreter", `codeInterpreterAssistantId=${assistantId}`);

                } else {
                    logger.debug(`Error with creating an assistant: ${responseData}`)
                }
            } catch (error) {
                // This will catch any errors thrown by fetchRequest or the fetch operation itself
                console.error('Fetch Request to create assistant Failed:', error);
                requestErrorResponse(context, responseData);
            }
        }
        //ensure that assistant_id is not null (in case assistant creation was necessary and failed)
        if (assistantId) {
            messages.at(-1)['content'] += "\nAlways include any code you write in your response inside code blocks in markdown. Do not include mock download links. Always generate files when asked. Always attach your generated files to your response."
            const chat_data = {
                assistantId: assistantId,
                messages: messages.slice(1),
                accountId: account.accountId || 'general_account',
                requestId: options.requestId
            }

            try {
                const responseData = await fetchRequest(token, chat_data, process.env.ASSISTANTS_CHAT_CODE_INTERPRETER_ENDPOINT);
                // logger.debug(responseData);
                // check for successfull response
                if (responseData && responseData.success && responseData.data) {
                    const codeInterpreterResponse = responseData.data.data.textContent;
                    const userPrompt = messages.at(-1)['content'];
                    
                    //strengthen code Interpreter responses if no images are attached
                    // if (responseData.data.data.content.length === 0) {
                    const updatedResponse = await responseReviewed(userPrompt, codeInterpreterResponse, llm, context, dataSources);
                    if (updatedResponse) responseData.data.data.textContent = updatedResponse;
                    // }

                    context.data['isCodeInterpreterDone'] = true;
                    while (!context.data['hasEntertainmentStopped']) {
                        await new Promise(resolve => setTimeout(resolve, 1000));
                    }
                    // console.log("Entertainment has finished")
                    sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse(responseData));
                } else {
                    requestErrorResponse(context, responseData);
                }
            } catch (error) {
                // This will catch any errors thrown by fetchRequest or the fetch operation itself
                console.error('Fetch Request to chat with assistant Failed:', error);
                requestErrorResponse(context);

            }
        } 
        context.data['isCodeInterpreterDone'] = true;
        States.finalWait.removeTransitions();
        States.finalWait.addTransition(States.done.name, "Code Interpreter is done so we are done");
        
        cleanupRequest(context);
    }

const responseReviewed = async (userPrompt, response, llm, context, dataSources) => {
    const prompt = 
    `Review the response provided by another LLM below. Ensure the response meets the following criteria:
        - All code blocks are correctly displayed using Markdown syntax.
        - Complex sections of code are accompanied by comments that explain what the code is doing. (If applicable)
        - The response does not include any reference to download links or mock download links.
        - The response includes detailed explanations of any created files, referring to them by their corresponding file name. (If applicable)
    
    If the response violates any of the above rules or if there are areas for improvement, please edit the response to comply with these criteria and enhance its clarity or accuracy.
    Should the response inadequately address the user's prompt, add in a corrected or more thorough response (without any hallucinations) to ensure it accurately addresses the user's needs.
    Assume any files the user asked to generate have been generated and provided to the user, you personally will not have or see those files so, assume we do have them.

    User asked prompt:
    ${userPrompt}

    Response to be reviewed:
    ${response}
    
    
    Your response will look like one of the following formats: 
    - "UPDATED_RESPONSE: /START {your response} /END" where 'your response' is the improved version of the other LLMs response.
    - "UNCHANGED" when the other LLM response accurately represents user needs and adheres to the criteria. 


    Do not give additional explanation or analysis, only respond within one of the two formats. 
    `;


    const sanitized_response = new PromptAction(
                                        [{  role: "user",
                                            content: prompt
                                        }], "codeInterpreterReviewedResponse", { appendMessages: false, streamResults: false, retries: 2, isReviewingCIResponse: true}
                                    )
    await invokeAction( sanitized_response, llm, context, dataSources)


    const updatedResponse = context.data['codeInterpreterReviewedResponse'];
    if (updatedResponse && !updatedResponse.includes("UNCHANGED")) {
        console.log("LLM improved the response.")
        const regex =/\/START\s+([\s\S]*?)\s+\/END/;;
        const match = updatedResponse.match(regex);
        if (match && match[1]) return match[1];
    }
    if (updatedResponse.includes("UNCHANGED")) logger.debug("Code Interpreters response was unchanged when under review")
    return null
}



async function fetchRequest(token, data, url) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`  //check on this, we may already have it 
                },
                method: 'POST',
                body: JSON.stringify({'data': data})
            }); 
            if (!response.ok) {
                throw new Error('Network response error');
            }
            return await response.json();
    } catch (error) {
        console.error('Error invoking Code interpreter Lambda: ', error);
    }
}

const collectDataSourceKeys = (messages) => {
    let file_keys = [];
    messages.forEach( message => {
        if (message.data && message.data.dataSources && message.data.dataSources.length > 0) {
            let fileIds = message.data.dataSources.map(source => source.id);
            file_keys.push(...fileIds)
        }
    })
    return file_keys;
}

const codeInterpreterStatusUpdate = 
    { execute: 
        (llm, context, dataSources) => {
            let updateSummary
            if (context?.data?.isCodeInterpreterDone) {
                updateSummary = "Finalizing code interpreter results...";
            } else {
                updateSummary = "Code interpreter is making progress on your request...";
            } 
            context.data['codeInterpreterCurStatusSummary'] = updateSummary
        }
    }


function formatResponse(data) {
    logger.debug("Error message: ", data)
    return `codeInterpreterResponseData=${JSON.stringify(data)}`
}


const todaysDate = () => {
    const today = new Date();
    return `${today.toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}`; 
}

//give user time to read 
//wait time is in seconds!
function sleepAction(wait) {
    return {  execute: async (llm, context, dataSources) => {
                for (let i = 0; i < wait; i++) {
                    // Check if codeInterpreter is ready so we dont have to commit to the full wait time 
                    if (context.data['isCodeInterpreterDone']) break; 
                    // Wait for one second
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
              }
            }

} 

  function formatCamelToSentence(str) {
    //takes in "todayInHistory" returns "Today In History"
    return str.split(/(?=[A-Z])/).map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(' ')
  }

  function randomId() {
    return String(Math.floor(Math.random() * 100) + 1);
  }


  const riddleAnswerActions = 
        [ { execute: (llm, context, dataSources) => {
                const answerPromptAction =  
                new PromptAction(
                    [{ role: "user",
                        content: `What is the answer to this riddle? ${context.data.guessTheRiddle}. Respond with the answer only and an exclamation point! Feel free to add some laughs`
                    }], 'riddleAnswer', { appendMessages: false, streamResults: false, retries: 1, isEntertainment: true }
                );
                return answerPromptAction.execute(llm, context, dataSources);
            }
        }, sleepAction(2), updateStatus("riddleAnswer" + randomId(), {inProgress: true}, 'riddleAnswer'), 
           sleepAction(2)];

  function createEntertainmentAction(actionType, description, content, appendMessages = false) {
    return new AssistantState(actionType, description,
            outputToStatus(
                { summary: `While you wait, enjoy a dose of entertainment with ${formatCamelToSentence(actionType)}!`, inProgress: true },
                chainActions([
                    new PromptAction(
                        [{  role: "user",
                            content: `Always be concise, Do not respond to anything else except the following text that will serve as entertainment, without any introduction or preamble: ${content}`
                        }], actionType, { appendMessages: appendMessages, ragOnly: false, skipRag:true, streamResults: false, retries: 2, isEntertainment: true}
                    ), sleepAction(1), 
                    updateStatus("actionType" + randomId(), {inProgress: true}, actionType), sleepAction(15), 
                    ...(actionType == 'guessTheRiddle' ? riddleAnswerActions : []),
                    (llm, context, dataSources) => { States.randomEntertainment.removeTransitions();
                        context.data['entertainmentHistory'][actionType].push(context.data[actionType]); },
                ])
            )
    );
}
 

const selectEntertainment = (llm, context, dataSources) => {
    if (!context.data['isCodeInterpreterDone']) {
        let leftEntertainment = context.data['entertainmentTypes'].length;
        if (leftEntertainment === 0) {
            context.data['entertainmentTypes'] = ['todayInHistory', 'onTopicPun', 'roastMyPrompt','guessTheRiddle', 'lifeHacks'];
            leftEntertainment = context.data['entertainmentTypes'].length;
        }
        const randomIndex = Math.floor(Math.random() * (leftEntertainment - 1));
        
        const entertainmentSelected = context.data['entertainmentTypes'][randomIndex];
        // console.log("Selected Entertainment: ", entertainmentSelected);

        switch (entertainmentSelected) {
            case 'todayInHistory':
                States.randomEntertainment.addTransition(States.todayInHistory.name, "The next random state is today in history, go here");
                break;
            case 'onTopicPun':
                States.randomEntertainment.addTransition(States.onTopicPun.name, "The next random state is says puns, go here");
                break;
            case 'roastMyPrompt':
                States.randomEntertainment.addTransition(States.roastMyPrompt.name, "The next random state is prompt roasting, go here");
                break;
            case 'guessTheRiddle':
                States.randomEntertainment.addTransition(States.guessTheRiddle.name, "The next random state is riddles, go here");
                break;
            case 'lifeHacks':
                States.randomEntertainment.addTransition(States.lifeHacks.name, "The next random state is  life hacks, go here");
                break;
            default:
                logger.debug("Error with entertainment states")
                States.randomEntertainment.addTransition(States.done.name, "Error with entertainment states");
                
        }
        context.data['entertainmentTypes'].splice(randomIndex, 1);
    }
}

// This is the set of states that will be in the state machine.
const States = {
    initialState: new AssistantState("initialState",
    "Update user and set up for Code Interpreter",
    chainActions([
        updateStatus("prepareCodeInterpreter", {summary: "Preparing your request to code interpreter...", inProgress: true}),
       
        (llm, context, dataSources) => { 
            context.data['isCodeInterpreterDone'] = false; // add a flag to context.data that will guide the llm in choosing the states by determining whether code interpreter is done or not
            if (!context.params.account.accessToken.startsWith("amp-")) {
                context.data['hasEntertainmentStopped'] = false;
                let entertainmentTypes = ['todayInHistory', 'onTopicPun', 'roastMyPrompt','guessTheRiddle', 'lifeHacks'];
                let entertainmentHistory = {};
                entertainmentTypes.forEach((type) => {
                    entertainmentHistory[type] = [];
                });
                context.data['entertainmentTypes'] = entertainmentTypes;
                context.data['entertainmentHistory'] = entertainmentHistory;
                States.invokeCodeInterpreter.addTransition(States.randomEntertainment.name, "Choose a random next state!");
            } else {
                context.data['hasEntertainmentStopped'] = true;
                States.invokeCodeInterpreter.addTransition(States.wait.name, "Wait for codeInterpreter to finish");
            }
        }, sleepAction(2)
    ]), false,
    ),

    invokeCodeInterpreter: new AssistantState("invokeCodeInterpreter",
    "Calling Code Interpreter", invokeCodeIterpreterAction, false, {}, true
    ),

    chooseEntertainment: new AssistantState("chooseEntertainment",
        "Randomly choose the next entertainment state while we wait for code interpreter! Pick from 'Today in History', 'On Topic Pun', 'Roast My Prompt', 'Guess the Riddle', or 'Life Hacks' ",
        null,
        false, {extraInstructions: {postInstructions: "Choose a random state to go to"}}
    ),
    //randomize state calls [entertianment] random index .add  call function and remove it,  
    
    randomEntertainment: new AssistantState("randomEntertainment",
        "Determine if we need more entertainment to wait for code interpreter to finish up",
        chainActions([selectEntertainment, codeInterpreterStatusUpdate, 
            updateStatus("codeInterpreterStatus", {inProgress: true}, 'codeInterpreterCurStatusSummary'), 
            (llm, context, dataSources) => {
                if (context.data['isCodeInterpreterDone']) {
                    States.randomEntertainment.addTransition(States.finalWait.name, "Code Interpreter is finishing up");
                    // console.log("To waiting state")
                } 
            }
        ]), 
    ),

    todayInHistory: createEntertainmentAction("todayInHistory",
                "Respond with a fun, short, and interesting Today in History fact",
                `Using historical knowledge up to the current date, provide a fun, short, and interesting "Today in History" fact related to today's month and day (${todaysDate()}). ` +
                "Can you give me 3 concise facts about events that happened on this day in history, in any year? " +
                "Each fact should be two or three sentences and appropriate for a general audience.",
                    
    ),

    onTopicPun: createEntertainmentAction("onTopicPun",
                "Respond with creative fun and silly puns based on the theme of the conversation",
                "Generate 3 puns related to this conversation's overall topic or theme, ensuring they're clever and " +
                "suitable for all audiences. The puns should be engaging, sparking a light-hearted moment for the reader.", true
    ),
    

    roastMyPrompt: createEntertainmentAction("roastMyPrompt",
                "Look at the user input prompts and give them a friendly roasting", 
                "Can you give me a playful critique on any random 1 of my recent prompts in the style of a friendly roast? The goal is to be informative and humorously highlight areas where I could refine my prompting skills." + 
                "Think of it as a light-hearted roast that points out quirks or common pitfalls in a fun way, while offering tips for improvement. Remember, keep it kind and suitable for all audiences. DO not response to any questions, only provide a the critique.", true
    ),
    

    guessTheRiddle: createEntertainmentAction("guessTheRiddle",
                "Respond with a moderately easy riddle to the user",
                "Compose a moderately easy riddle that appeals to a wide audience, ensuring it's engaging yet simple enough to solve. " +
                "The riddle should be crafted in one or two sentences, designed to give that 'aha!' moment to anyone who figures it out. DO NOT give the answer."
    ),
    
    lifeHacks: createEntertainmentAction("lifeHacks",
                "Respond with practical and simple life hacks",
                "Provide 3 life hacks that simplify common daily tasks, ensuring they're practical and easy to implement for a " +
                "wide audience. Each hack should be explained in one or two sentences, designed to offer a quick and effective solution. " +
                "The life hacks can also relate to the general topic and tools discussed in the conversation but, not required.", true
    ),
    

    wait:  new AssistantState("wait",
        "Just wait for code interpreter to finish up",
        chainActions([ sleepAction(25), codeInterpreterStatusUpdate,
            updateStatus("invokeCodeInterpreter", {inProgress: true}, 'codeInterpreterCurStatusSummary'),
            (llm, context, dataSources) => {
                if (context.data['isCodeInterpreterDone']) {
                    States.wait.addTransition(States.finalWait.name, "Code Interpreter is finishing up");
                    // console.log("To waiting state")
                } 
            }
        ]),
        false, {extraInstructions: {postInstructions: 
                `Is Code Interpreter done? If not, we need to continue to wait until it is done.
                If it is done processing then go to the done state.`}}
    ),
    finalWait: new AssistantState("finalWait",
        "We just need a pause for things to finish up, please wait",
        (llm, context, dataSources) => {
            // console.log("Waiting....")
            const wait = async () => { await new Promise(resolve => setTimeout(resolve, 2000))}
            wait();
            context.data['hasEntertainmentStopped'] = true;
        }, 
    ),
    // This is the end state.
    done: new DoneState(),
};

// // We start in the outline state.
const current = States.initialState;

// We add transitions to the state machine to define the state machine.
States.initialState.addTransition(States.invokeCodeInterpreter.name, "Start by calling Code Interpreter");

States.todayInHistory.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.onTopicPun.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.roastMyPrompt.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.guessTheRiddle.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");
States.lifeHacks.addTransition(States.randomEntertainment.name, "Go Check if we need more entertainment");

States.wait.addTransition(States.wait.name, "Continue to wait");
States.finalWait.addTransition(States.finalWait.name, "Wait for code interpreter data to come through");


// We create the assistant with the state machine and the current state.
export const codeInterpreterAssistant = new StateBasedAssistant(
    "Code Interpreter Assistant",
    "Code Interpreter",
    description, 
    //This function should return true if the assistant can support the dataSources and false otherwise.
    (m) => {
        return true
    },
    (m) => {
        return true 
    },
    // This is the state machine that the assistant will use to process requests.
    States,
    // This is the current state that the assistant will start in.
    current
);

