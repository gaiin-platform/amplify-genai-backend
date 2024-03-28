import {
    AssistantState, chainActions,
    DoneState, USER_INPUT_STATE, UserInputState,outputToStatus, invokeAction, 
    PromptAction, StateBasedAssistant, updateStatus
} from "./statemachine/states.js";
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("codeInterpreterAssitant");
const PROVIDER = process.env.ASSISTANTS_OPENAI_PROVIDER;
const description = `This assistants  executes Python in a secure sandbox, handling diverse data to craft files and visual graphs.
It tackles complex code and math challenges through iterative problem-solving, refining failed attempts into successful executions.
Use this for complex mathmatical operations and coding tasks that involve the need to run the code in a sandbox environment.`;

const additionalPrompt = `You have access to a sandboxed environment for writing and testing code. When you are asked to create a visualization you should follow these steps:
                            1. Write the code.
                            2. Anytime you write new code display a preview of the code to show your work.
                            3. Run the code to confirm that it runs.
                            4. If the code is successful display the visualization.
                            5. If the code is unsuccessful, try to revise the code and rerun going through the steps from above again.`

const requestErrorResponse = (context) => {
    sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse( { success: false, error: 'Internal error when making code interpreter request.'} ))

}

//function call to code_interpeter endpoints in an action type format
const invokeCodeIterpreterAction = 
    // { execute: 
    async (llm, context, dataSources) => {
        if (!(context)) return; 
        const token = context.params.account.accessToken;
        const options = context.body.options
        // if we have a codeInterpreterId then we can chat if we dont then we have to create first 

        // The conversation currently does have an assistantID in our database (which contains an assistantID with code_interpreter)
        let assistantId = options.codeInterpreterAssistantId || null;
        //openai
        //assistantId = 
        //azure 
        assistantId = 'ast/70164c001d436929b031c20f6e8930bd5c9f92ed22b7e96e33ee34330890f5ce/ab9dc548-0128-4b1f-8c4f-8e472c2ff182'
        /*
        if we have a conversation assistant id and it for some reason doesnt work we need to create a new assistant
        then in the front end update the conversations code interpreter assistant id
        */
        for (let i = 0; i < 2; i++) {
            // The conversation currently does not have an assistantID in our database 
            if (assistantId === null) {
                
                const create_data = {
                    access_token: token,
                    name: "codeInterpreter",
                    description: description, 
                    tags: [],
                    instructions:   options.prompt + additionalPrompt,
                    dataSources: context.data.dataSources || [], // see which has all the datasources in the entire conversation 
                    tools: [{"type": "code_interpreter"}], 
                    provider: PROVIDER // can be 'azure' or 'openai'
                }

                try {
                    const responseData = await fetchRequest(token, create_data, process.env.ASSISTANTS_CREATE_CODE_INTERPRETER_ENPOINT); 

                    if (responseData && responseData.success) {
                        assistantId = responseData.data.assistantId
                        //we need to ensure we send the assistant_id back to be saved in the conversation
                        sendDeltaToStream(context.responseStream, "codeInterpreter", `codeInterpreterAssistantId=${assistantId}`);
                        sendDeltaToStream(context.responseStream, "codeInterpreter", "");

                    } else {
                        logger.debug(`Error with creating an assistant: ${responseData}`)
                    }
                } catch (error) {
                    // This will catch any errors thrown by fetchRequest or the fetch operation itself
                    console.error('Fetch Request to create assistant Failed:', error);
                    requestErrorResponse(context);
                }
            }
            //ensure that assistant_id is not null (in case assistant creation was necessary and failed)
            if (assistantId) {
                const chat_data = {
                    id: assistantId,
                    messages: context.body.messages.slice(1),
                    fileKeys: context.data.conversationDataSources  || []//context.activeDataSources
                }

                try {
                    const responseData = await fetchRequest(token, chat_data, getInterpreterUrl());
                    logger.debug(`Response data from chatting with code interpreter: ${responseData}`)

                    if (responseData.success && responseData.data) {
                        sendDeltaToStream(context.responseStream, "codeInterpreter", formatResponse(responseData));
                    } else {
                        requestErrorResponse(context);
                    }
                    break;
                } catch (error) {
                    // This will catch any errors thrown by fetchRequest or the fetch operation itself
                    console.error('Fetch Request to chat with assistant Failed:', error);
                    requestErrorResponse(context);

                }
            } 
            //think about in case the id is no good then loop to create a new one
            assistantId = null;
        }

        // to context.data that will guide the llm in choosing the states by determining whether code interpreter is done or not
        context.data['isCodeInterpreterDone'] = true;

    }
// }



function getInterpreterUrl() {
    // note: i see in assistant provider is included, may come from there
    if (PROVIDER === 'openai') {
        return process.env.ASSISTANTS_OPENAI_CODE_INTERPRETER_ENDPOINT
    } else if (PROVIDER === 'azure') {
        return process.env.ASSISTANTS_AZURE_CODE_INTERPRETER_ENDPOINT
    } else {
        throw new Error('Invalid provider specified.');
    }
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
    return `codeInterpreterResponseData=${JSON.stringify(data)}`
}


const todaysDate = () => {
    const today = new Date();
    return `${today.getMonth()} ${today.getDate()}`; 
}


const sleepAction = {
    execute: async (llm, context, dataSources) => {
            await new Promise(resolve => setTimeout(resolve, 3000)); 
    }
  }

  function formatCamelToSentence(str) {
    //takes in "todayInHistory" returns "Today In History"
    return str.split(/(?=[A-Z])/).map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(' ')
  }

  function createEntertainmentAction(actionType, description, content) {
    return new AssistantState(actionType, description,
            outputToStatus(
                { summary: `You selected ${formatCamelToSentence(actionType)}!`, inProgress: true },
                chainActions([
                    new PromptAction(
                        [{
                            role: "assistant",
                            content: `Make sure not to repeat the same entertainment as before.
                            If you see some data under the key "${actionType}", read it and ensure
                            not to repeat the same phrase and overall vibe/message of the value set there.
                            If you dont see that key, disregard this message. \n\n Only provide one
                            response for the task. \n\n`+ content
                        }], actionType, { appendMessages: false, streamResults: false, retries: 1, isEntertainment: true }
                    ),
                    updateStatus(actionType, {inProgress: true}, actionType),

                ])
            )
    );
}


// This is the set of states that will be in the state machine.
const States = {
    initialState: new AssistantState("initialState",
    "Update user and set up for Code Interpreter",
    chainActions([
        updateStatus("prepareCodeInterpreter", {summary: "Preparing your request to code interpreter...", inProgress: true}),
        // add a flag to context.data that will guide the llm in choosing the states by determining whether code interpreter is done or not
        (llm, context, dataSources) => { context.data['isCodeInterpreterDone'] = false }, 
    ]), false,
    ),

    invokeCodeInterpreter: new AssistantState("invokeCodeInterpreter",
    "Calling Code Interpreter", invokeCodeIterpreterAction, false, {}, true
    ),

    chooseEntertainment: new UserInputState("chooseEntertainment",
        "Choose entertainment...",
        "Choose your entertainment while you wait! Pick from 'Today in History', 'On Topic Pun', 'Roast My Prompt', 'Riddle', 'Life Hacks' or let me know if you would rather wait.",
        `The entertainment option chosen by the user will lead you to the next corresponding state. The 
        entertainment options are Today in History, On Topic Pun, Roast My Prompt, Riddle, Life Hacks or rather wait. 
        If the user inputs they do not want entertainment, then go to iRatherWait state.
        If user does not pick within the options then go back to the chooseEntertainment state.`,
        false, {appendMessages: false, streamResults: false, skipRag: true, ragOnly: false}
    ),
    
    moreEntertainment: new AssistantState("moreEntertainment",
        "Determine if we need more entertainment to wait for code interpreter to finish up",
        chainActions([codeInterpreterStatusUpdate,
            updateStatus("codeInterpreterStatus", {inProgress: true}, 'codeInterpreterCurStatusSummary'),
             sleepAction, sleepAction, sleepAction,sleepAction, 
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,
             sleepAction, sleepAction, sleepAction,sleepAction,

        ]), 
        
        false, {extraInstructions: {postInstructions: `Have user choose from the entertainment list again?
                Is Code Interpreter done? If not, we need to continue to entertain the user until it is done.
                If it is done processing then go to the done state otherwise have the user input their choice 
                of entertaininment at the chooseEntertainment state`}}
    ),

    todayInHistory: createEntertainmentAction("todayInHistory",
                "Respond with a fun, short, and interesting Today in History fact",
                `Provide a a few concise 'Today in History' fact related to today's date, covering a significant 
                or unique events in one or two sentences each for a general audience. Today is ${todaysDate()}`,
                    
    ),

    onTopicPun: createEntertainmentAction("onTopicPun",
                "Respond with creative fun and silly puns based on the theme of the conversation",
                `Generate a few puns related to this conversation's overall topic or theme, ensuring they're clever and 
                suitable for all audiences. The puns should be engaging, sparking a light-hearted moment for the reader.`
    ),
    

    roastMyPrompt: createEntertainmentAction("roastMyPrompt",
                "Look at the user input prompts and give them a roast",
                `Craft light-hearted roasts about the users input prompt provided, keeping it witty and suitable for all 
                audiences. The roast should playfully critique the prompt's theme or content in one or two sentences while 
                being informative while suggesting tips to enhance the users prompt crafting skills.`
    ),
    

    guessTheRiddle: createEntertainmentAction("guessTheRiddle",
                "Respond with a moderately easy riddle to the user",
                `Compose a moderately easy riddle that appeals to a wide audience, ensuring it's engaging yet simple enough to solve. 
                The riddle should be crafted in one or two sentences, designed to give that 'aha!' moment to anyone who figures it out.`
    ),
    
    lifeHacks: createEntertainmentAction("lifeHacks",
                "Respond with practical and simple life hacks",
                `Provide a few life hacks that simplify common daily tasks, ensuring they're practical and easy to implement for a 
                wide audience. Each hack should be explained in one or two sentences, designed to offer a quick and effective solution.`
    ),
    

    iRatherWait:  new AssistantState("iRatherWait",
        "Just wait for code interpreter to finish up",
        chainActions([ sleepAction, codeInterpreterStatusUpdate,
            updateStatus("invokeCodeInterpreter", {inProgress: true}, 'codeInterpreterCurStatusSummary'),
        ]),
        false, {extraInstructions: {postInstructions: 
                `Is Code Interpreter done? If not, we need to continue to wait until it is done.
                If it is done processing then go to the done state.`}}
    ),
    // This is the end state.
    done: new DoneState(),
};



// We start in the outline state.
const current = States.initialState;

States.initialState.addTransition(States.invokeCodeInterpreter.name, "Start by calling Code Interpreter");

//test
States.invokeCodeInterpreter.addTransition(States.moreEntertainment.name, "Start by calling Code Interpreter");
States.moreEntertainment.addTransition(States.moreEntertainment.name, "Code Interpreter NOT done, so go here");
States.moreEntertainment.addTransition(States.done.name, "Code Interpreter IS DONE so lets go output the response");


/*

// We add transitions to the state machine to define the state machine.

States.invokeCodeInterpreter.addTransition(States.chooseEntertainment.name, "Start by calling Code Interpreter");

States.chooseEntertainment.addTransition(USER_INPUT_STATE, "While we wait on code interpreter to finish, lets ask the user what they want for entertaiment");
States.chooseEntertainment.addTransition(States.chooseEntertainment.name, "User did not choose within the options, so ask them to choose their entertainment again");

States.chooseEntertainment.addTransition(States.todayInHistory.name, "If the user_input says today in history, go here");
States.chooseEntertainment.addTransition(States.onTopicPun.name, "If the user_input says puns, go here");
States.chooseEntertainment.addTransition(States.roastMyPrompt.name, "If the user_input says prompt roasting, go here");
States.chooseEntertainment.addTransition(States.guessTheRiddle.name, "If the user_input says riddles, go here");
States.chooseEntertainment.addTransition(States.lifeHacks.name, "If the user_input says life hacks, go here");
States.chooseEntertainment.addTransition(States.iRatherWait.name, "If the user_input says they do not want entertainment or rather wait, go here");


States.todayInHistory.addTransition(States.moreEntertainment.name, "Go Check if we need more entertainment");
States.onTopicPun.addTransition(States.moreEntertainment.name, "Go Check if we need more entertainment");
States.roastMyPrompt.addTransition(States.moreEntertainment.name, "Go Check if we need more entertainment");
States.guessTheRiddle.addTransition(States.moreEntertainment.name, "Go Check if we need more entertainment");
States.lifeHacks.addTransition(States.moreEntertainment.name, "Go Check if we need more entertainment");

States.iRatherWait.addTransition(States.iRatherWait.name, "Code Interpreter is not done so we going to keep waiting");
States.iRatherWait.addTransition(States.responseCodeInterpreterResults.name, "Code Interpreter is done so lets go output the response");


States.moreEntertainment.addTransition(States.chooseEntertainment.name, "If Code Interpreter is not done then go back to chooseEntertainment");
States.moreEntertainment.addTransition(States.done.name, "Code Interpreter is done so we are done");

*/

// We create the assistant with the state machine and the current state.
export const codeInterpreterAssistant = new StateBasedAssistant(
    "Code Interpreter Assistant",
    "Code Interpreter Assistant",
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

