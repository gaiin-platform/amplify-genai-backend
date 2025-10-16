# Router.js Deep Code Analysis: Main Request Processing Flow

## Overview
- **File**: `amplify-lambda-js/router.js`
- **Analysis Focus**: Lines 90-224 (Main request processing else statement)
- **Purpose**: Main chat request routing and processing pipeline for AI conversations
- **Context**: Entry point for validated chat requests that have passed authentication, rate limiting, and request type validation

---

## Line-by-Line Analysis: Main Request Processing Flow (Lines 90-224)

### **[Lines 90-98] Request Entry Point & User Model Retrieval**

**Line 90**: `} else {`
- **Purpose**: Entry point for valid chat requests (passed all prior validation checks)
- **Prerequisites**: 
  - User is authenticated (`params.user` exists)
  - Not rate limited 
  - Not a killswitch request
  - Not a datasource-only request
  - Has valid request body with messages

**Line 91**: `const user_model_data = await getUserAvailableModels(params.accessToken);`
- **Purpose**: Retrieves available AI models for the authenticated user
- **Function Call**: `getUserAvailableModels()` - **REQUIRES DEEP ANALYSIS**
- **Input**: `params.accessToken` (JWT or API key token)
- **Expected Output**: 
  ```javascript
  {
    models: {modelId: modelConfig, ...},
    cheapest: modelConfig,
    advanced: modelConfig, 
    documentCaching: modelConfig
  }
  ```
- **Critical**: Determines what AI models user can access (OpenAI, Claude, etc.)

**Line 92**: `const models = user_model_data.models;`
- **Purpose**: Extracts models object from user model data
- **Data Type**: Object mapping `modelId → modelConfig`
- **Example**: `{"gpt-4": {config...}, "claude-3": {config...}}`

**Lines 93-98**: Models availability validation
```javascript
if (!models) {
    returnResponse(responseStream, {
        statusCode: 400,
        body: {error: "No user models."}
    });
}
```
- **Purpose**: Validates user has access to at least one AI model
- **Critical Path**: Early exit point if user has no model access
- **Error Response**: HTTP 400 "No user models."
- **Flow**: If no models → terminate request with error

### **[Lines 101-109] Request Options Processing & Prompt Calculation**

**Line 101**: `logger.debug("Processing request");`
- **Purpose**: Debug logging for request processing start

**Line 103**: `let options = params.body.options ? {...params.body.options} : {};`
- **Purpose**: Creates mutable copy of request options or empty object
- **Critical**: Shallow clone to avoid mutating original request
- **Data**: Contains model selection, settings, feature flags, etc.

**Lines 105-108**: Prompt count calculation
```javascript
const calculatedPrompts = params.body.messages ? Math.ceil(params.body.messages.length / 2) : 0;
params.body.options.numberPrompts = calculatedPrompts;
options.numberPrompts = calculatedPrompts;
```
- **Purpose**: Calculates conversation prompt pairs for billing/tracking
- **Logic**: `messages.length / 2` assumes user/assistant message pairs
- **Critical**: Sets `numberPrompts` in **both** original and copied options
- **Usage**: Likely used for cost calculation and rate limiting

**Line 110**: `const modelId = (options.model && options.model.id);`
- **Purpose**: Extracts requested model ID from options
- **Safety**: Uses optional chaining to handle missing model object
- **Example**: `"gpt-4"`, `"claude-3-sonnet"`, etc.

### **[Lines 112-124] Model Validation & Configuration Setup**

**Line 112**: `const model = models[modelId];`
- **Purpose**: Retrieves specific model configuration from available models
- **Lookup**: Uses `modelId` as key to get model config object
- **Result**: Model configuration object or `undefined`

**Lines 114-119**: Model existence validation
```javascript
if (!model) {
    returnResponse(responseStream, {
        statusCode: 400,
        body: {error: "Invalid model."}
    });
}
```
- **Purpose**: Validates requested model exists in user's available models
- **Critical Path**: Second early exit point - ensures model access
- **Error Response**: HTTP 400 "Invalid model."
- **Security**: Prevents users from accessing unauthorized models

**Lines 121-123**: Model parameter override
```javascript
params.model = model;
options.model = model;
```
- **Purpose**: Replaces client-provided model data with backend-verified model config
- **Critical**: Ensures model config comes from **trusted backend source**, not client
- **Security**: Prevents model parameter tampering/injection

**Lines 125-128**: Fallback model assignment
```javascript
params.cheapestModel = user_model_data.cheapest ?? model;
params.advancedModel = user_model_data.advanced ?? model;
params.documentCachingModel = user_model_data.documentCaching ?? model;
```
- **Purpose**: Sets fallback models for different use cases
- **Fallback Logic**: Use specialized models if available, otherwise default to selected model
- **Use Cases**:
  - `cheapestModel`: For cost-optimized operations
  - `advancedModel`: For complex reasoning tasks
  - `documentCachingModel`: For document processing with caching

**Lines 130-132**: Options model assignment
```javascript
options.cheapestModel = getModelByType(params, ModelTypes.CHEAPEST);
options.advancedModel = getModelByType(params, ModelTypes.ADVANCED);  
options.documentCachingModel = getModelByType(params, ModelTypes.DOCUMENT_CACHING);
```
- **Purpose**: Populates options with resolved model configurations
- **Function Call**: `getModelByType()` - **REQUIRES ANALYSIS**
- **Pattern**: Uses `ModelTypes` enum for type safety

### **[Lines 135-149] Request Body Reconstruction & Data Source Setup**

**Line 135**: `let body = {...params.body, options: options, model: model.id};`
- **Purpose**: Creates new request body with updated options and verified model ID
- **Critical**: Merges original body with backend-verified model configuration
- **Override**: `options` and `model` now contain trusted backend data

**Line 136**: `logger.debug("Checking access on data sources");`
- **Purpose**: Debug logging for data source access validation phase

**Line 137**: `let dataSources = [...params.body.dataSources];`
- **Purpose**: Creates mutable copy of requested data sources array
- **Safety**: Shallow clone to avoid mutating original request
- **Data Type**: Array of data source identifiers/configurations

**Line 138**: `logger.info("Request options.", options);`
- **Purpose**: Logs final request options for debugging/auditing
- **Visibility**: INFO level - visible in production logs

**Line 140**: `delete body.dataSources;`
- **Purpose**: Removes data sources from body (will be processed separately)
- **Reason**: Data sources require authorization check before inclusion

**Lines 142-145**: Chat function definition
```javascript
logger.debug("Determining chatFn");
const chatFn = async (body, writable, context) => {
    return await getChatFn(model, body, writable, context);
}
```
- **Purpose**: Creates async wrapper for model-specific chat function
- **Function Call**: `getChatFn()` - **REQUIRES ANALYSIS**
- **Closure**: Captures `model` from outer scope
- **Interface**: Standardized `(body, writable, context)` signature

**Lines 147-149**: Data sources default initialization
```javascript
if (!params.body.dataSources) {
    params.body.dataSources = [];
}
```
- **Purpose**: Ensures data sources array exists (defensive programming)
- **Default**: Empty array if no data sources provided

### **[Lines 151-165] Data Source Resolution & Authorization**

**Lines 151-153**: Data source logging and resolution
```javascript
logger.info("Request data sources", dataSources);
dataSources = await resolveDataSources(params, body, dataSources);
```
- **Purpose**: Resolves and authorizes data source access
- **Function Call**: `resolveDataSources()` - **REQUIRES DEEP ANALYSIS**
- **Critical**: Authorization checkpoint - can fail with 401
- **Input**: `params` (user context), `body` (request), `dataSources` (requested sources)

**Lines 155-157**: Data source debug logging
```javascript
for (const ds of [...dataSources, ...(body.imageSources ?? [])]) {
    console.debug("Resolved data source: ", ds.id, "\n". ds);
}
```
- **Purpose**: Logs all resolved data sources (including image sources)
- **Bug**: Line 156 has syntax error: `"\n". ds` should be `"\n", ds`
- **Coverage**: Includes both regular data sources and image sources

**Lines 159-165**: Data source authorization error handling
```javascript
} catch (e) {
    logger.error("Unauthorized access on data sources: " + e);
    return returnResponse(responseStream, {
        statusCode: 401,
        body: {error: "Unauthorized data source access."}
    });
}
```
- **Purpose**: Handles authorization failures during data source resolution
- **Critical Path**: Third early exit point - data source access denied
- **Error Response**: HTTP 401 "Unauthorized data source access."
- **Security**: Prevents unauthorized access to protected data sources

### **[Lines 167-189] Stream Setup & Request State Initialization**

**Lines 167-169**: Trace stream setup
```javascript
if (doTrace) {
    responseStream = new TraceStream({}, responseStream);
}
```
- **Purpose**: Wraps response stream with tracing capabilities if enabled
- **Condition**: `doTrace` = `process.env.TRACING_ENABLED === 'true'` (Line 19)
- **Function Call**: `TraceStream()` constructor - imported from `./common/streams.js`
- **Enhancement**: Adds request tracing without modifying stream interface

**Line 171**: `logger.debug("Calling chat with data");`
- **Purpose**: Debug logging before core chat processing begins

**Line 173**: `const requestId = getRequestId(params);`
- **Purpose**: Extracts or generates unique request identifier  
- **Function Call**: `getRequestId()` - **REQUIRES ANALYSIS** (defined at line 23)
- **Logic**: `(params.body.options && params.body.options.requestId) || params.user`
- **Fallback**: Uses user ID if no specific request ID provided

**Lines 175-185**: Assistant parameters object construction
```javascript
const assistantParams = {
    account: {
        user: params.user,
        accessToken: params.accessToken,
        accountId: options.accountId,
        apiKeyId: params.apiKeyId
    },
    model,
    requestId,
    options
};
```
- **Purpose**: Creates standardized parameter object for assistant execution
- **Structure**: Account context + model + request tracking + options
- **Usage**: Passed to assistant for user context and configuration

**Lines 188-189**: Request state tracking
```javascript
const initSegment = segment.addNewSubsegment('chat-js.router.init');
await createRequestState(params.user, requestId);
```
- **Purpose**: Creates X-Ray subsegment and initializes request state tracking
- **Function Call**: `createRequestState()` - **REQUIRES DEEP ANALYSIS**
- **Tracking**: Enables request cancellation and status monitoring
- **X-Ray**: Performance monitoring and tracing

### **[Lines 191-211] LLM & Assistant Processing**

**Lines 191-194**: LLM wrapper initialization  
```javascript
const llm = new LLM(
    chatFn,
    assistantParams,
    responseStream);
```
- **Purpose**: Creates LLM wrapper with chat function, params, and stream
- **Class**: `LLM` class - imported from `./common/llm.js`
- **Encapsulation**: Provides unified interface to different AI models
- **Streaming**: Connected to response stream for real-time output

**Lines 196-201**: Assistant selection and timing
```javascript
const now = new Date();
const assistant = await chooseAssistantForRequest(llm, model, body, dataSources);
const assistantSelectionTime = new Date() - now;
sendStateEventToStream(responseStream, {routingTime: assistantSelectionTime});
sendStateEventToStream(responseStream, {assistant: assistant.name});
```
- **Purpose**: Selects appropriate assistant and tracks selection performance
- **Function Call**: `chooseAssistantForRequest()` - **REQUIRES DEEP ANALYSIS**
- **Timing**: Measures assistant selection performance
- **Streaming**: Sends routing time and assistant name to client
- **Function Call**: `sendStateEventToStream()` - sends metadata to stream

**Line 201**: `initSegment.close();`
- **Purpose**: Closes X-Ray initialization subsegment

### **[Lines 204-224] Assistant Execution & Response Handling**

**Lines 204-211**: Assistant handler execution
```javascript
const chatSegment = segment.addNewSubsegment('chat-js.router.assistantHandler');
const response = await assistant.handler(
    llm,
    assistantParams,
    body,
    dataSources,
    responseStream);
chatSegment.close();
```
- **Purpose**: Executes the selected assistant with all required parameters
- **X-Ray**: Creates performance monitoring subsegment for assistant execution  
- **Critical**: Core AI processing happens in `assistant.handler()`
- **Parameters**: LLM wrapper, account params, request body, data sources, stream
- **Return**: May return JSON response or stream directly

**Lines 214-217**: Trace saving (if enabled)
```javascript
if(doTrace) {
    trace(requestId, ["response"], {stream: responseStream.trace})
    await saveTrace(params.user, requestId);
}
```
- **Purpose**: Saves request trace data for debugging/analysis
- **Function Calls**: `trace()` and `saveTrace()` - from `./common/trace.js`
- **Condition**: Only if tracing is enabled
- **Storage**: Persists trace data linked to user and request

**Lines 219-224**: Final response handling
```javascript  
if (response) {
    logger.debug("Returning a json response that wasn't streamed from chatWithDataStateless");
    logger.debug("Response", response);
    returnResponse(responseStream, response);
} 
```
- **Purpose**: Handles non-streamed responses from assistants
- **Condition**: Some assistants return JSON instead of streaming
- **Logging**: Debug info about non-streamed responses
- **Output**: Sends JSON response through response stream

---

## Functions Requiring Deep Analysis

### 1. `getUserAvailableModels(accessToken)` - Line 91
- **File Location**: TBD (needs investigation)  
- **Purpose**: Retrieves user's available AI models based on access permissions
- **Critical**: Determines entire available model set for user
- **Status**: 🔍 **PENDING DEEP DIVE**

### 2. `getModelByType(params, ModelTypes.X)` - Lines 130-132
- **File Location**: `./common/params.js` (from imports)
- **Purpose**: Resolves model configuration by type (CHEAPEST, ADVANCED, DOCUMENT_CACHING)
- **Input**: `params` object with model fallbacks, `ModelTypes` enum value
- **Status**: 🔍 **PENDING DEEP DIVE**

### 3. `getChatFn(model, body, writable, context)` - Line 144
- **File Location**: `./common/params.js` (from imports)
- **Purpose**: Returns model-specific chat function for AI conversation
- **Input**: Model config, request body, stream, execution context
- **Status**: 🔍 **PENDING DEEP DIVE**

### 4. `resolveDataSources(params, body, dataSources)` - Line 153  
- **File Location**: `./datasource/datasources.js` (from imports)
- **Purpose**: Resolves and authorizes access to requested data sources
- **Critical**: Authorization checkpoint - can throw on unauthorized access
- **Status**: 🔍 **PENDING DEEP DIVE**

### 5. `getRequestId(params)` - Line 173
- **File Location**: Same file, line 23-25
- **Purpose**: Extracts request ID from options or falls back to user ID
- **Logic**: `(params.body.options?.requestId) || params.user`
- **Status**: ✅ **ANALYZED** (simple utility function)

### 6. `createRequestState(user, requestId)` - Line 189
- **File Location**: `./requests/requestState.js` (from imports)
- **Purpose**: Initializes request state tracking for cancellation/monitoring
- **Critical**: Enables killswitch functionality and request management
- **Status**: 🔍 **REQUIRES DEEP DIVE**

### 7. `chooseAssistantForRequest(llm, model, body, dataSources)` - Line 197
- **File Location**: `./assistants/assistants.js` (from imports) 
- **Purpose**: Selects appropriate assistant based on request characteristics
- **Critical**: Core routing logic - determines which AI assistant handles request
- **Status**: 🔍 **REQUIRES DEEP DIVE**

### 8. `sendStateEventToStream(responseStream, data)` - Lines 199-200
- **File Location**: `./common/streams.js` (from imports)
- **Purpose**: Sends metadata events to response stream (routing time, assistant name)
- **Usage**: Client-side progress/status updates
- **Status**: 🔍 **REQUIRES ANALYSIS**

### 9. `trace()` and `saveTrace()` - Lines 215-216
- **File Location**: `./common/trace.js` (from imports)
- **Purpose**: Records and persists request trace data for debugging
- **Condition**: Only when `TRACING_ENABLED=true`
- **Status**: 🔍 **REQUIRES ANALYSIS**

### 10. LLM Class Constructor - Lines 191-194
- **File Location**: `./common/llm.js` (from imports)
- **Purpose**: Unified interface wrapper for different AI models
- **Parameters**: `chatFn`, `assistantParams`, `responseStream`
- **Status**: 🔍 **REQUIRES DEEP DIVE**

---

## Critical Data Flow Summary - Complete Main Processing Flow (Lines 90-224)

### **Phase 1: User & Model Validation (Lines 90-132)**
1. **Entry Point**: Valid chat request enters main processing (Line 90)
2. **Model Access Retrieval**: `getUserAvailableModels(accessToken)` → get user's AI model permissions
3. **Model Availability Check**: Validate user has access to at least one model → exit 400 if none
4. **Request Options Processing**: Clone and enhance options with prompt calculations  
5. **Model Selection**: Extract requested `modelId` and validate against user's models → exit 400 if invalid
6. **Model Configuration**: Override with backend-verified model configs + set fallback models
7. **Type-specific Models**: Resolve cheapest/advanced/documentCaching model variants

### **Phase 2: Request Body & Data Source Processing (Lines 135-165)**  
8. **Body Reconstruction**: Create new body with verified model + updated options
9. **Data Source Preparation**: Clone data sources array for authorization processing
10. **Chat Function Setup**: Create model-specific chat function wrapper with closure
11. **Data Source Authorization**: `resolveDataSources()` → authorize access → exit 401 if unauthorized
12. **Data Source Logging**: Debug log all resolved sources (including image sources)

### **Phase 3: Execution Setup & Initialization (Lines 167-189)**
13. **Stream Enhancement**: Wrap with `TraceStream` if tracing enabled
14. **Request Tracking**: Generate/extract `requestId` and create request state tracking
15. **Assistant Parameters**: Build standardized params object with account context
16. **LLM Wrapper**: Initialize `LLM` class with chat function, params, and stream

### **Phase 4: Assistant Selection & Execution (Lines 191-224)**
17. **Assistant Selection**: `chooseAssistantForRequest()` → determine appropriate AI assistant
18. **Performance Tracking**: Measure assistant selection time → stream to client  
19. **Assistant Metadata**: Stream assistant name to client for UI updates
20. **Core Processing**: Execute `assistant.handler()` with full context → **MAIN AI PROCESSING**
21. **Trace Persistence**: Save request trace data if tracing enabled
22. **Response Handling**: Send JSON response if assistant returned non-streamed data

### **Critical Exit Points & Error Handling**
- **Exit 400**: No user models available (Line 94-97)
- **Exit 400**: Invalid/unauthorized model selection (Line 115-118)  
- **Exit 401**: Unauthorized data source access (Line 161-164)
- **Exception Handling**: Global try/catch → 400 with error message (Lines 226-233)

### **Key Security Validations**
- ✅ User authentication (validated before Line 90)
- ✅ Rate limiting (validated before Line 90)  
- ✅ Model authorization (user can only access permitted models)
- ✅ Data source authorization (prevents unauthorized data access)
- ✅ Backend config override (prevents client-side model tampering)

---

## Key Variables Tracking - Complete Flow

### **Core Data Objects**
- `user_model_data`: Complete model access data retrieved from backend (Line 91)
- `models`: Available model configurations object `{modelId: modelConfig}` (Line 92)
- `modelId`: Client-requested model identifier string (Line 110)
- `model`: Backend-verified model configuration object (Line 112)
- `options`: Cloned and enhanced request options (Line 103)
- `body`: Reconstructed request body with verified model data (Line 135)
- `dataSources`: Authorized data sources array (Line 137 → 153)

### **Request Context & Tracking**
- `calculatedPrompts`: Conversation prompt pairs count `Math.ceil(messages.length / 2)` (Line 106)
- `requestId`: Unique request identifier for tracking/cancellation (Line 173)
- `assistantParams`: Standardized parameter object for assistant execution (Lines 175-185)
- `chatFn`: Model-specific chat function wrapper with closure (Lines 143-145)

### **Processing & Performance**
- `llm`: LLM wrapper class instance for unified AI model interface (Lines 191-194)
- `assistant`: Selected AI assistant object from routing logic (Line 197)
- `assistantSelectionTime`: Performance metric for assistant selection (Line 198)
- `response`: Final response from assistant (optional, Line 205)

### **Model Configuration Hierarchy**
- `params.model`: Primary selected model (Line 122)
- `params.cheapestModel`: Cost-optimized model fallback (Line 126)
- `params.advancedModel`: Advanced reasoning model fallback (Line 127)  
- `params.documentCachingModel`: Document processing model fallback (Line 128)
- `options.cheapestModel/advancedModel/documentCachingModel`: Resolved model configs (Lines 130-132)

### **Streaming & Tracing**
- `responseStream`: Response stream (optionally wrapped with TraceStream) (Line 168)
- `doTrace`: Tracing enabled flag `process.env.TRACING_ENABLED === 'true'` (Line 19)
- `segment/subSegment`: AWS X-Ray performance monitoring segments (Lines 188, 204)

---

## Analysis Completion Status

✅ **COMPLETE**: Router.js Main Processing Flow Analysis (Lines 90-224)

### **What We Documented:**
- **134 lines** of critical request processing code
- **22 step** detailed data flow with 4 distinct phases
- **10 key functions** identified for deep analysis
- **5 critical exit points** and security validations
- **15+ key variables** tracked through the entire flow
- **1 bug identified**: Line 156 syntax error (`"\n". ds`)

### **Key Insights Discovered:**
1. **Security-First Architecture**: Multiple validation layers prevent unauthorized access
2. **Model Authorization**: Backend overrides prevent client-side model tampering
3. **Streaming Architecture**: Real-time response with performance monitoring
4. **Assistant Pattern**: Pluggable AI assistant selection based on request characteristics
5. **Request State Tracking**: Enables cancellation and monitoring capabilities
6. **Performance Monitoring**: AWS X-Ray integration throughout processing pipeline

### **Next Analysis Targets** (Priority Order):
1. 🎯 **`chooseAssistantForRequest()`** - Core routing logic
2. 🎯 **`getUserAvailableModels()`** - Model permission system  
3. 🎯 **`resolveDataSources()`** - Data source authorization
4. 🎯 **`createRequestState()`** - Request tracking system
5. 🎯 **LLM Class** - AI model wrapper interface

*Analysis Status: ✅ Lines 90-224 COMPLETE - Ready for function deep dives*

---

## 🎯 DEEP DIVE #1: `chooseAssistantForRequest()` - Core Routing Logic

### **Function Overview**
- **File**: `amplify-lambda-js/assistants/assistants.js`
- **Lines**: 208-315 (108 lines)
- **Purpose**: Core AI assistant selection logic that determines which assistant handles the request
- **Critical**: This is the brain of the routing system - determines the entire conversation experience

### **Function Signature & Entry Point (Lines 208-213)**

```javascript
export const chooseAssistantForRequest = async (llm, model, body, dataSources, assistants = defaultAssistants) => {
    logger.info(`Choose Assistant for Request `);

    const clientSelectedAssistant = body.options?.assistantId ?? null;

    let selectedAssistant = null;
```

**Parameters Analysis:**
- `llm`: LLM wrapper instance for AI processing and streaming
- `model`: Backend-verified model configuration object
- `body`: Request body with messages and options
- `dataSources`: Authorized data sources array
- `assistants`: Available assistants (defaults to `defaultAssistants` array)

**Initial State Setup:**
- `clientSelectedAssistant`: User's explicitly requested assistant ID (Line 211)
- `selectedAssistant`: Will hold the final selected assistant (Line 213)

### **Phase 1: Client-Selected Assistant Processing (Lines 214-233)**

```javascript
if (clientSelectedAssistant) {
    logger.info(`Client Selected Assistant: `, clientSelectedAssistant);
    // For group ast
    const user = llm.params.account.user;
    const token = llm.params.account.accessToken;
    
    selectedAssistant = await getUserDefinedAssistant(user, defaultAssistant, clientSelectedAssistant, token);
    if (!selectedAssistant) {
        llm.sendStatus(newStatus({   
            inProgress: false,
            message: "Selected Assistant Not Found",
            icon: "assistant",
            sticky: true
        }));
        llm.forceFlush();

        if (body.options.api_accessed) {
            throw new Error("Provided Assistant ID is invalid or user does not have access to this assistant.");
        }
    }
}
```

**Purpose**: Handles user-requested specific assistants
**Critical Function Call**: `getUserDefinedAssistant()` - **REQUIRES DEEP ANALYSIS**
- **Input**: User ID, default fallback, assistant ID, access token
- **Output**: User-defined assistant object or null if not found/authorized
**Error Handling**: 
- Sends error status to client stream
- **API Mode**: Throws error for invalid assistant (Line 231-232)
- **UI Mode**: Shows error status but continues processing

### **Phase 2: Tool-Based Assistant Selection (Lines 234-249)**

```javascript
} else if (getTools(body.messages).length > 0) {
    logger.info("Using tools");
    // Note: tools are added in fillInAssistant in order to support tool use for client selected Assistants

    selectedAssistant = fillInAssistant(
        {
            name: "Amplify Automation",
            instructions: agentInstructions,
            description: "Amplify Automation",
            data: {
                opsLanguageVersion: "v4",
            }
        },
        defaultAssistant
    )
}
```

**Purpose**: Auto-selects tool-enabled assistant when tools are detected in messages
**Critical Function Calls**: 
- `getTools(body.messages)` - **REQUIRES ANALYSIS** - extracts tools from conversation
- `fillInAssistant()` - **REQUIRES ANALYSIS** - creates enhanced assistant with tool capabilities
**Assistant Configuration**: Creates "Amplify Automation" assistant with:
- Tool execution capabilities  
- Agent instructions from `agentInstructions`
- Operations language version v4

### **Phase 3: Special Mode Assistant Selection (Lines 250-256)**

```javascript
} else if (body.options.codeInterpreterOnly && (!body.options.api_accessed)) {
    selectedAssistant = await codeInterpreterAssistant(defaultAssistant);
    //codeInterpreterAssistant;
} else if (body.options.artifactsMode && (!body.options.api_accessed)) {
    selectedAssistant = ArtifactModeAssistant;
    console.log("ARTIFACT MODE DETERMINED")
}
```

**Code Interpreter Mode**: 
- **Condition**: `codeInterpreterOnly` flag + not API accessed
- **Function Call**: `codeInterpreterAssistant()` - **REQUIRES ANALYSIS**
- **Purpose**: Enables Python code execution capabilities

**Artifacts Mode**:
- **Condition**: `artifactsMode` flag + not API accessed  
- **Assistant**: `ArtifactModeAssistant` - predefined assistant for artifact generation
- **Purpose**: Specialized for creating downloadable artifacts

### **Phase 4: Automatic Assistant Selection (Lines 259-292)**

```javascript
if (selectedAssistant === null) {
    const status = newStatus({inProgress: true, message: "Choosing an assistant to help..."});
    llm.sendStatus(status);
    llm.forceFlush();

    // Look for any body.messages.data.state.currentAssistant going in reverse order through the messages
    // and choose the first one that is found.
    const currentAssistant = body.messages.map((m) => {
        return (m.data && m.data.state && m.data.state.currentAssistant) ? m.data.state.currentAssistant : null;
    }).reverse().find((a) => a !== null);

    // Hack to make AWS lambda send the status update and not buffer
    let availableAssistants = getAvailableAssistantsForDataSources(model, dataSources, assistants);

    if (availableAssistants.some((a) => a.name === currentAssistant) &&
        (!dataSources || dataSources.length === 0)) {
        // Future, we can automatically default to the last used assistant to speed things
        // up unless some predetermined condition is met.
        availableAssistants = [assistants.find((a) => a.name === currentAssistant)]
    }

    const start = new Date().getTime();
    const selectedAssistantName = (availableAssistants.length > 1 ) ?
        await chooseAssistantForRequestWithLLM(llm, body, dataSources,
            availableAssistants) : availableAssistants[0].name;
    const timeToChoose = new Date().getTime() - start;
    logger.info(`Selected assistant ${selectedAssistantName}`);
    logger.info(`Time to choose assistant: ${timeToChoose}ms`);

    selectedAssistant = assistants.find((a) => a.name === selectedAssistantName);

    status.inProgress = false;
    llm.sendStatus(status);
}
```

**Purpose**: Automatic assistant selection when no explicit choice was made

**Conversation History Analysis (Lines 266-268)**:
- Searches through messages in reverse order
- Looks for `message.data.state.currentAssistant` 
- **Optimization**: Attempts to reuse last assistant for consistency

**Available Assistants Filtering (Line 271)**:
- **Function Call**: `getAvailableAssistantsForDataSources()` - **REQUIRES ANALYSIS**
- Filters assistants by data source compatibility and model support

**Last Assistant Optimization (Lines 273-278)**:
- If current assistant from history is available AND no data sources
- **Performance Optimization**: Skip LLM selection, use last assistant directly

**LLM-Based Selection (Lines 281-283)**:
- **Condition**: Multiple assistants available
- **Function Call**: `chooseAssistantForRequestWithLLM()` - **REQUIRES DEEP ANALYSIS** 
- Uses AI to intelligently select best assistant for the task
- **Performance Tracking**: Measures selection time

**Fallback Selection**: If only one assistant available, use it directly

### **Phase 5: Final Assistant Setup & Response (Lines 294-315)**

```javascript
const selected = selectedAssistant || defaultAssistant;

logger.info("Sending State Event to Stream ", selectedAssistant.name);
let stateInfo = {
    currentAssistant: selectedAssistant.name,
    currentAssistantId: clientSelectedAssistant || selectedAssistant.name,
}
if (selectedAssistant.disclaimer) stateInfo = {...stateInfo, currentAssistantDisclaimer : selectedAssistant.disclaimer};

llm.sendStateEventToStream(stateInfo);

llm.sendStatus(newStatus(
    {
        inProgress: false,
        message: "The \"" + selected.displayName + " Assistant\" is responding.",
        icon: "assistant",
        sticky: true
    }));
llm.forceFlush();

return selected;
```

**Fallback Protection**: Ensures `defaultAssistant` is used if selection fails (Line 294)

**State Broadcasting (Lines 297-303)**:
- Sends assistant selection info to client stream
- **currentAssistant**: Selected assistant name
- **currentAssistantId**: Original client request or selected name  
- **currentAssistantDisclaimer**: Optional disclaimer text

**Client Status Update (Lines 305-312)**:
- Shows final "Assistant is responding" message
- Uses assistant's `displayName` for user-friendly text
- **forceFlush()**: Ensures immediate delivery to client

**Return**: Final selected assistant object

---

## 🔍 NESTED FUNCTION ANALYSIS REQUIRED

### **Critical Functions Called by `chooseAssistantForRequest()`:**

#### **1A. `getAvailableAssistantsForDataSources()` - Line 271 (Lines 195-205)**

```javascript
export const getAvailableAssistantsForDataSources = (model, dataSources, assistants = defaultAssistants) => {
    console.log("getAvailableAssistantsForDataSources function")

    // if (!dataSources || dataSources.length === 0) {
    //     return [defaultAssistant];
    // }

    return assistants.filter((assistant) => {
        return assistant.handlesDataSources(dataSources) && assistant.handlesModel(model);
    });
}
```

**Purpose**: Filters available assistants based on data source and model compatibility
**Logic**: 
- Tests each assistant's `handlesDataSources(dataSources)` method
- Tests each assistant's `handlesModel(model)` method  
- **Both conditions must be true** for assistant to be available
**Return**: Array of compatible assistants
**Note**: Commented fallback logic suggests original intent to return `[defaultAssistant]` for no data sources

#### **1B. `chooseAssistantForRequestWithLLM()` - Line 282 (Lines 113-178)**

```javascript
export const chooseAssistantForRequestWithLLM = async (llm, body, dataSources, assistants = defaultAssistants) => {
    // console.log(chooseAssistantForRequestWithLLM);

    const messages = [
        {
            "role": "system",
            "content": `
            Help the user choose the best assistant for the task.
            You only need to output the name of the assistant. YOU MUST
            honor the user's choice if they request a specific assistant.
            `
        },
        // [commented out user message section]
    ];

    const prompt = `
Think step by step how to perform the task. What are the steps? 
Which assistant is the best fit to solve the given task based on the
steps? Is the user asking for a specific assistant?

If you are not sure, please choose the default assistant.

${buildAssistantDescriptionMessages(assistants)}
${buildDataSourceDescriptionMessages(dataSources)}

Please choose the best assistant to help with the task:
---------------
${body.messages.slice(-1)[0].content}
---------------
`;
    const model = body.options.advancedModel;
    const updatedBody = {messages, options:{ model }};

    const names = assistants.map((a) => a.name);

    const chatFn = async (body, writable, context) => {
        return await getChatFn(model, body, writable, context);
    }
    const llmClone = llm.clone(chatFn);

    //return await llm.promptForChoice({messages, options:{model}}, names, []);
    const result = await llmClone.promptForData(updatedBody, [], prompt,
        {bestAssistant:names.join("|")}, null, (r) => {
       return r.bestAssistant && assistants.find((a) => a.name === r.bestAssistant);
    }, 3);

    return result.bestAssistant || defaultAssistant.name;
}
```

**Purpose**: Uses AI (LLM) to intelligently select the best assistant for a given task
**Key Elements**:
- **System Message**: Instructs AI to select assistant name only
- **Dynamic Prompt**: Builds contextual prompt with assistant descriptions and data sources  
- **Model**: Uses `advancedModel` for selection decision (higher reasoning capability)
- **Validation**: Ensures selected assistant exists in available list
- **Fallback**: Returns `defaultAssistant.name` if selection fails

**Helper Functions Called**:
- `buildAssistantDescriptionMessages(assistants)` - formats assistant options
- `buildDataSourceDescriptionMessages(dataSources)` - formats data source context
- `llm.clone(chatFn)` - creates new LLM instance for selection
- `llmClone.promptForData()` - structured data extraction from LLM

**Data Extraction**: Uses structured output format `{bestAssistant: "name"}` with validation

#### **1C. Helper Functions for Assistant Descriptions (Lines 79-94, 96-111)**

```javascript
export const buildDataSourceDescriptionMessages = (dataSources) => {
    if (!dataSources || dataSources.length === 0) {
        return "";
    }

    const descriptions = dataSources.map((ds) => {
        return `${ds.id}: (${ds.type})`;
    }).join("\n");

    return `
    The following data sources are available for the task:
    ---------------
    ${descriptions}
    --------------- 
    `;
}

export const buildAssistantDescriptionMessages = (assistants) => {
    if (!assistants || assistants.length === 0) {
        return [];
    }

    const descriptions = assistants.map((assistant) => {
        return `name: ${assistant.name} - ${assistant.description}`;
    }).join("\n");

    return `
    The following assistants are available to work on the task:
    ---------------
    ${descriptions}
    --------------- 
    `;
}
```

**Purpose**: Format context information for LLM assistant selection
**Data Source Format**: `"id: (type)"` - shows available data with types
**Assistant Format**: `"name: assistantName - description"` - shows capabilities

#### **2A. `getUserDefinedAssistant()` - Line 220 (userDefinedAssistants.js:221-252)**

```javascript
export const getUserDefinedAssistant = async (current_user, assistantBase, assistantPublicId, token) => {
    const ast_owner = assistantPublicId.startsWith("astgp") ? await getAstgGroupId(assistantPublicId) : current_user;
    
    if (!ast_owner) return null;

    // verify the user has access to the group since this is a group assistant
    if (assistantPublicId.startsWith("astgp") && current_user !== ast_owner) {
        console.log( `Checking if ${current_user} is a member of group: ${ast_owner}`);
        if (!isMemberOfGroup(current_user, ast_owner, token)) return null;
    }
    let assistantData = null;
    const assistantAlias = await getAssistantByAlias(ast_owner, assistantPublicId);

    if (assistantAlias) {
        assistantData = await getAssistantByAssistantDatabaseId(
            assistantAlias.data.id
        );
        console.log("Assistant found by alias: ", assistantData);
    } else {
        //check if ast is standalone
        assistantData = await getStandaloneAst(assistantPublicId, current_user, token);
        console.log("Assistant found by standalone ast: ", assistantData);
    }

    if (assistantData) {
        const userDefinedAssistant =  fillInAssistant(assistantData, assistantBase)
        console.log(`Client Selected Assistant: `, userDefinedAssistant.displayName)
        return userDefinedAssistant;
    }

    return null;
};
```

**Purpose**: Retrieves and validates access to user-defined custom assistants
**Critical Authorization Logic**:
- **Group Assistants**: `assistantPublicId.startsWith("astgp")` → requires group membership validation
- **Alias Lookup**: Attempts to find assistant by alias first (primary method)
- **Standalone Lookup**: Falls back to standalone assistant lookup
- **Access Control**: Multiple layers of permission checking (group membership, ownership, public access)

**Database Operations**:
- `getAssistantByAlias()` - DynamoDB lookup in ASSISTANTS_ALIASES_DYNAMODB_TABLE
- `getAssistantByAssistantDatabaseId()` - DynamoDB lookup in ASSISTANTS_DYNAMODB_TABLE  
- `getStandaloneAst()` - Complex authorization check for standalone assistants
- `isMemberOfGroup()` - Group membership validation with API calls

**Security Features**:
- Group ownership validation
- Public/private assistant access control
- Amplify group integration validation
- Token-based API authorization

#### **2B. `fillInAssistant()` - Line 238 (userDefinedAssistants.js:255-578)**

```javascript
export const fillInAssistant = (assistant, assistantBase) => {
    return {
        name: assistant.name,
        displayName: assistant.name,
        handlesDataSources: (ds) => { return true; },
        handlesModel: (model) => { return true; },
        description: assistant.description,
        disclaimer: assistant.disclaimer ?? '',
        handler: async (llm, params, body, ds, responseStream) => {
            // [Complex 300+ line handler implementation]
        }
    };
}
```

**Purpose**: Transforms raw assistant data into executable assistant object with enhanced capabilities
**Key Features**:
- **Universal Compatibility**: Handles all data sources and models
- **Dynamic Handler**: Creates custom handler with user-defined instructions and capabilities
- **Enhanced Configuration**: Supports advanced features like RAG, tools, integrations

**Handler Capabilities** (Lines 269-576):
- **RAG Control**: `skipRag`, `ragOnly` options
- **Data Source Integration**: Metadata insertion, download links
- **Message Enhancement**: Timezone info, message IDs, references
- **Tool Integration**: Operations language versions (v1-v4), custom operations
- **Agent Integration**: v4 operations trigger full agent workflow execution
- **Template Processing**: Dynamic instruction templating with context
- **Integration Support**: API credential handling for external services

**Critical v4 Agent Path** (Lines 414-459):
- **Condition**: `assistant.data.opsLanguageVersion === "v4"`  
- **Action**: Calls `invokeAgent()` instead of standard LLM processing
- **Result**: Terminates with `llm.endStream()` - completely different execution path

#### **2C. `getTools()` - Line 234 (agent.js)**

```javascript
export const getTools = (messages) => {
    const lastMessage = messages.slice(-1)[0];
    return lastMessage.configuredTools ?? [];
}
```

**Purpose**: Extracts configured tools from the latest message in conversation
**Simple Logic**: Returns `configuredTools` array from last message or empty array
**Usage**: Determines if tool-enabled assistant should be automatically selected

#### **2D. `codeInterpreterAssistant()` - Line 251 (codeInterpreter.js)**

```javascript
export const codeInterpreterAssistant = async (assistantBase) => {
    return {
        name: 'Code Interpreter Assistant',
        displayName: 'Code Interpreter ',
        handlesDataSources: (ds) => { return true; },
        handlesModel: (model) => { return true; },
        description: [description],
        disclaimer: '',
        handler: async (llm, params, body, ds, responseStream) => {
            // [Complex code execution handler]
        }
    };
}
```

**Purpose**: Creates specialized assistant for Python code execution in sandboxed environment
**Capabilities**:
- **Sandbox Execution**: Secure Python code execution
- **File Generation**: Creates and returns files (PNG, PDF, CSV)
- **Iterative Problem Solving**: Refines failed attempts into successful executions
- **Mathematical Operations**: Complex calculations and data analysis
- **Visualization**: Graph and chart generation

**Use Cases**:
- User explicitly requests code interpreter
- File generation tasks (PNG, PDF, CSV)
- Complex mathematical operations
- Data analysis and visualization

---

## 🎯 DEEP DIVE #1 ANALYSIS COMPLETE

### **Summary: `chooseAssistantForRequest()` Function Analysis**

✅ **Main Function**: 108 lines analyzed with 5 distinct phases
✅ **Nested Functions**: 6 critical functions analyzed in depth
✅ **Database Operations**: 4+ DynamoDB tables involved in assistant lookup
✅ **Security Model**: Multi-layer authorization with group/permission validation
✅ **Execution Paths**: 5 different assistant selection strategies identified
✅ **Integration Points**: Agent framework, code interpreter, tool systems

### **Key Architecture Insights**:

1. **Hierarchical Selection Logic**: Client → Tools → Special Modes → Automatic → Default
2. **Security-First Design**: Multiple authorization layers prevent unauthorized assistant access
3. **Performance Optimization**: Conversation history analysis avoids redundant LLM calls
4. **Pluggable Architecture**: Assistant system supports custom, group, and specialized assistants
5. **Agent Integration**: v4 operations completely bypass standard LLM processing
6. **Tool Detection**: Automatic assistant enhancement based on conversation content

---

## 📋 REMAINING DEEP DIVE TARGETS

### **🎯 Priority 1: Core Infrastructure**
1. **`getUserAvailableModels()`** - Model permission system (Line 91 in router)
2. **`resolveDataSources()`** - Data source authorization (Line 153 in router)  
3. **`createRequestState()`** - Request tracking system (Line 189 in router)
4. **LLM Class** - AI model wrapper interface (Lines 191-194 in router)

### **🎯 Priority 2: Processing Functions**
5. **`getChatFn()`** - Model-specific chat function provider
6. **`getModelByType()`** - Model type resolution
7. **`sendStateEventToStream()`** - Client streaming updates
8. **`trace()` and `saveTrace()`** - Request tracing system

### **🎯 Priority 3: Assistant System Deep Dive**
9. **Assistant Handler Interface** - Standard assistant execution pattern
10. **`defaultAssistant.handler()`** - Core chat processing logic (Lines 32-58)
11. **`mapReduceAssistant`** - Large document processing assistant
12. **`getDataSourcesByUse()`** - Data source processing and filtering

*Analysis Status: ✅ DEEP DIVE #1 COMPLETE - `chooseAssistantForRequest()` fully analyzed*

---

## 🎯 DEEP DIVE #2: `getUserAvailableModels()` - Model Permission System

### **Function Overview**
- **File**: `amplify-lambda-js/models/models.js`
- **Lines**: 34-81 (48 lines)
- **Purpose**: Critical model permission system that determines which AI models user can access
- **API Endpoint**: Calls `/available_models` endpoint on `chat-billing` service
- **Import**: Imported on line 14 of `router.js`: `import {getUserAvailableModels} from "./models/models.js"`

### **Function Signature & Entry Point (Lines 34-36)**

```javascript
export const getUserAvailableModels = async (accessToken) => {
    const apiUrl = process.env.API_BASE_URL + '/available_models'; 
```

**Parameters Analysis:**
- `accessToken`: JWT token or API key for user authentication
- **API Endpoint**: `${API_BASE_URL}/available_models` - calls chat-billing service
- **HTTP Method**: GET request with Bearer token authentication

### **Phase 1: HTTP Request & Authentication (Lines 37-49)**

```javascript
const response = await fetch(apiUrl, {
    method: "GET",
    headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer "+accessToken
    },
});

if (!response.ok) {
    console.error("Error fetching ops: ", response.statusText);
    return [];
}
```

**Purpose**: Makes authenticated HTTP request to backend model service
**Authentication**: Bearer token passed in Authorization header
**Error Handling**: Returns empty array on HTTP failure
**Critical**: This is where user model access permissions are resolved

### **Phase 2: Response Validation & Data Extraction (Lines 50-56)**

```javascript
const data = await response.json();

if(!data || !data.success || !data.data || !data.data.models) {
    console.error("Missing data in user available models response: ", response.statusText);
    return [];
}
```

**Purpose**: Validates API response structure and extracts model data
**Expected Structure**: 
```javascript
{
    success: true,
    data: {
        models: [...],
        default: {...},
        advanced: {...},
        cheapest: {...},
        documentCaching: {...}
    }
}
```
**Error Handling**: Returns empty array if response structure is invalid

### **Phase 3: Model Data Transformation & Optimization (Lines 57-62)**

```javascript
const modelsMap = data.data.models.reduce((acc, model) => {
    acc[model.id] = ensureNumericProperties(model); // Use the model's `id` as the key
    return acc;
}, {});

const model_data = {...data.data, models: modelsMap};
```

**Purpose**: Transforms models array into optimized lookup object
**Transformation**: `[{id: "gpt-4", ...}, ...]` → `{"gpt-4": {...}, "claude-3": {...}}`
**Optimization**: Creates O(1) model lookup by ID for router.js validation
**Data Sanitization**: Applies `ensureNumericProperties()` to each model

### **Phase 4: Numeric Property Sanitization (Lines 64-74)**

```javascript
// Apply ensureNumericProperties to all model_data properties except 'models'
for (const key in model_data) {
    if (key !== 'models' && model_data[key]) {
        try {
            model_data[key] = ensureNumericProperties(model_data[key]);
        } catch (error) {
           console.error("Error ensuring numeric properties for ", model_data[key], "\nError: ", error);
        }
    }
}
```

**Purpose**: Ensures numeric fields are properly typed across all model configurations
**Target Fields**: `inputContextWindow`, `outputTokenLimit`, `inputTokenCost`, `outputTokenCost`, `cachedTokenCost`
**Safety**: Try-catch prevents bad data from breaking the entire response
**Application**: Processes `default`, `advanced`, `cheapest`, `documentCaching` models

### **Phase 5: Model Fallback Resolution (Lines 76-80)**

```javascript
// if default is null, we will override it with the user chosen model in router.
if (!model_data.advanced) model_data.advanced = model_data.default;
if (!model_data.cheapest) model_data.cheapest = model_data.default;
if (!model_data.documentCaching) model_data.documentCaching = model_data.cheapest;
```

**Purpose**: Provides intelligent fallbacks when specialized models aren't configured
**Fallback Hierarchy**:
1. `advanced` → `default` (if advanced reasoning model not configured)
2. `cheapest` → `default` (if cost-optimized model not configured) 
3. `documentCaching` → `cheapest` (document processing falls back to cheapest)
**Router Usage**: These fallbacks are used in router.js lines 126-132 for type-specific model selection

---

## 🔍 NESTED FUNCTION ANALYSIS: `ensureNumericProperties()`

### **Helper Function Analysis (Lines 9-32)**

```javascript
const ensureNumericProperties = (model) => {
    if (!model) return model;
    
    const numericFields = [
        'inputContextWindow',
        'outputTokenLimit',
        'inputTokenCost',
        'outputTokenCost', 
        'cachedTokenCost'
    ];
    
    const sanitizedModel = { ...model };
    
    numericFields.forEach(field => {
        if (sanitizedModel[field] !== undefined && sanitizedModel[field] !== null) {
            const numValue = Number(sanitizedModel[field]);
            if (!isNaN(numValue)) {
                sanitizedModel[field] = numValue;
            }
        }
    });
    
    return sanitizedModel;
};
```

**Purpose**: Converts string-based numeric values to actual numbers for reliable calculations
**Critical Fields**:
- `inputContextWindow`: Max tokens for input context
- `outputTokenLimit`: Max tokens for response
- `inputTokenCost`/`outputTokenCost`/`cachedTokenCost`: Pricing calculations
**Data Source**: Backend often returns strings from DynamoDB - requires conversion
**Safety**: Only converts valid numeric strings, preserves non-numeric values
**Usage**: Applied to every model configuration to ensure mathematical operations work correctly

---

## 🌐 BACKEND IMPLEMENTATION: `/available_models` Endpoint

### **Service Location**: `chat-billing/service/core.py` (Lines 141-212)

### **Endpoint Configuration**
- **Path**: `/available_models`
- **Method**: GET
- **Authentication**: Bearer token via `@validated(op="read")`
- **Function**: `get_user_available_models()`
- **Tables**: `MODEL_RATE_TABLE`, `AMPLIFY_ADMIN_DYNAMODB_TABLE`

### **Core Authorization Logic (Lines 161-167)**

```python
# Filter and format the available models directly using a list comprehension
available_models = [
    extract_data(model_id, model_data)
    for model_id, model_data in supported_models
    if (model_data.get("isAvailable", False)
        or bool(set(model_data.get("exclusiveGroupAvailability", [])) & set(affiliated_groups or []))
    )
]
```

**Authorization Logic**:
1. **Public Models**: `isAvailable: true` - accessible to all users
2. **Group-Restricted Models**: `exclusiveGroupAvailability: ["group1", "group2"]` - only specific groups
3. **Group Membership**: Uses `get_user_affiliated_groups(token)` to check user's groups
4. **Set Intersection**: User's groups ∩ model's allowed groups determines access

### **Model Data Sources & Structure (Lines 171-212)**

**Default Models Resolution**:
1. **Primary Source**: `AMPLIFY_ADMIN_DYNAMODB_TABLE` with `config_id: "defaultModels"`
2. **Fallback**: Direct query of `MODEL_RATE_TABLE` with boolean flags
3. **Auto-Migration**: Updates admin table when using fallback method

**Model Categories**:
- `default` (user): Primary model for standard operations
- `advanced`: Higher reasoning capability model  
- `cheapest`: Cost-optimized model
- `documentCaching`: Specialized for document processing

### **Model Data Extraction (Lines 285-300)**

```python
def extract_data(model_id, model_data):
    return {
        "id": model_id,
        "name": model_data["name"],
        "description": model_data.get("description", ""),
        "inputContextWindow": model_data.get("inputContextWindow", -1),
        "outputTokenLimit": model_data.get("outputTokenLimit", -1),
        "supportsImages": model_data.get("supportsImages", False),
        "supportsReasoning": model_data.get("supportsReasoning", False),
        "provider": model_data.get("provider", ""),
        "supportsSystemPrompts": model_data.get("supportsSystemPrompts", False),
        "systemPrompt": model_data.get("systemPrompt", ""),
        "inputTokenCost": model_data.get("inputTokenCost", 0),
        "outputTokenCost": model_data.get("outputTokenCost", 0),
        "cachedTokenCost": model_data.get("cachedTokenCost", 0),
    }
```

**Model Configuration Fields**:
- **Identity**: `id`, `name`, `description`, `provider`
- **Capabilities**: `supportsImages`, `supportsReasoning`, `supportsSystemPrompts`
- **Limits**: `inputContextWindow`, `outputTokenLimit`
- **Pricing**: `inputTokenCost`, `outputTokenCost`, `cachedTokenCost`
- **Customization**: `systemPrompt` for additional instructions

### **Group-Based Model Access System**

**Implementation Flow**:
1. **User Token** → `get_user_affiliated_groups()` → User's group memberships
2. **Model Config** → `exclusiveGroupAvailability: ["premium", "enterprise"]` → Allowed groups  
3. **Access Check** → Set intersection determines if user can access model
4. **Response** → Only authorized models included in response

**Use Cases**:
- **Premium Models**: Restrict expensive models to paying customers
- **Beta Models**: Limit access to testing groups
- **Enterprise Models**: Custom models for specific organizations
- **Regional Restrictions**: Geographic access control

---

## 🎯 DEEP DIVE #2 ANALYSIS COMPLETE

### **Summary: `getUserAvailableModels()` Function Analysis**

✅ **Main Function**: 48 lines analyzed across 5 distinct phases
✅ **Helper Function**: `ensureNumericProperties()` numeric data sanitization
✅ **Backend Integration**: Complete `/available_models` endpoint analysis  
✅ **Authorization System**: Group-based model access control documented
✅ **Data Flow**: Frontend request → Backend authorization → Model filtering → Response transformation

### **Key Architecture Insights:**

1. **Security-First Design**: Server-side model authorization prevents client-side model access tampering
2. **Group-Based Permissions**: Sophisticated model access control using user group memberships
3. **Fallback Strategy**: Intelligent model defaults ensure system always has valid configurations
4. **Data Sanitization**: Robust numeric field conversion handles backend data inconsistencies
5. **Performance Optimization**: Model array → lookup object transformation for O(1) access in router
6. **Cost Management**: Model pricing integration enables usage tracking and billing

### **Critical Dependencies:**
- **Backend Service**: `chat-billing` service `/available_models` endpoint
- **Authentication**: Bearer token for user identification and group resolution
- **Database Tables**: `MODEL_RATE_TABLE` (model configs) + `AMPLIFY_ADMIN_DYNAMODB_TABLE` (defaults)
- **Group System**: User group memberships determine model access permissions

---

## 📋 REMAINING DEEP DIVE TARGETS (Updated)

### **🎯 Priority 1: Core Infrastructure**
1. ✅ **`getUserAvailableModels()`** - Model permission system - **COMPLETED**
2. **`resolveDataSources()`** - Data source authorization (Line 153 in router)  
3. **`createRequestState()`** - Request tracking system (Line 189 in router)
4. **LLM Class** - AI model wrapper interface (Lines 191-194 in router)

### **🎯 Priority 2: Processing Functions**
5. **`getChatFn()`** - Model-specific chat function provider
6. **`getModelByType()`** - Model type resolution
7. **`sendStateEventToStream()`** - Client streaming updates
8. **`trace()` and `saveTrace()`** - Request tracing system

### **🎯 Priority 3: Assistant System Deep Dive**
9. **Assistant Handler Interface** - Standard assistant execution pattern
10. **`defaultAssistant.handler()`** - Core chat processing logic (Lines 32-58)
11. **`mapReduceAssistant`** - Large document processing assistant
12. **`getDataSourcesByUse()`** - Data source processing and filtering

*Analysis Status: ✅ DEEP DIVE #2 COMPLETE - `getUserAvailableModels()` fully analyzed*

---

## 🎯 DEEP DIVE #3: `resolveDataSources()` - Data Source Authorization System

### **Function Overview**
- **File**: `amplify-lambda-js/datasource/datasources.js`
- **Lines**: 373-418 (46 lines)
- **Purpose**: Critical data source authorization system that validates and authorizes access to user documents and files
- **Called From**: Router line 153: `dataSources = await resolveDataSources(params, body, dataSources);`
- **Import**: Line 10 of router.js: `import {resolveDataSources} from "./datasource/datasources.js"`

### **Function Signature & Entry Point (Lines 373-374)**

```javascript
export const resolveDataSources = async (params, body, dataSources) => {
    logger.info("Resolving data sources", {dataSources: dataSources});
```

**Parameters Analysis:**
- `params`: User context with `accessToken`, `user` ID, and other request parameters
- `body`: Request body containing messages and options
- `dataSources`: Array of requested data sources to authorize and resolve
- **Return**: Authorized and resolved data sources array

### **Phase 1: Image Source Separation & Processing (Lines 376-388)**

```javascript
// separate the image ds
if (body && body.messages && body.messages.length > 0) {
    const lastMsg = body.messages[body.messages.length - 1];
    const ds = lastMsg.data && lastMsg.data.dataSources;
    if (ds) {
        body.imageSources = ds.filter(d => isImage(d));
    } else if (body.options?.api_accessed){ // support images coming from the /chat endpoint
        const imageSources = dataSources.filter(d => isImage(d));
        if (imageSources.length > 0) body.imageSources = imageSources;
    }
    console.log("IMAGE: body.imageSources", body.imageSources);
}

dataSources = dataSources.filter(ds => !isImage(ds))
```

**Purpose**: Separates image data sources from document data sources for different processing
**Image Detection**: Uses `isImage(ds)` → `ds.type.startsWith("image/")`
**Image Sources**: 
- **Primary**: From last message `data.dataSources` 
- **Fallback**: From API endpoint direct image attachments
- **Processing**: Images stored in `body.imageSources`, removed from main `dataSources`
**Rationale**: Images require different authorization and storage handling than documents

### **Phase 2: Data Source Translation & Hash Resolution (Lines 390-400)**

```javascript
dataSources = await translateUserDataSourcesToHashDataSources(params, body, dataSources);

const convoDataSources = await translateUserDataSourcesToHashDataSources(
    params, body, getDataSourcesInConversation(body, true)
);

let allDataSources = [
    ...dataSources,
    ...convoDataSources.filter(ds => !dataSources.find(d => d.id === ds.id))
]
```

**Purpose**: Translates user-specific data source IDs to global hash-based IDs for deduplication
**Key Functions**: 
- `translateUserDataSourcesToHashDataSources()` - **REQUIRES DEEP ANALYSIS**
- `getDataSourcesInConversation()` - extracts data sources from conversation history
**Data Deduplication**: Maps user files → global hash IDs for shared file system
**Conversation Integration**: Includes data sources referenced throughout conversation
**Unique Filtering**: Prevents duplicate data sources in combined array

### **Phase 3: User Ownership Detection & Authorization Check (Lines 402-415)**

```javascript
const nonUserSources = allDataSources.filter(ds =>
    !extractKey(ds.id).startsWith(params.user + "/")
);

if (nonUserSources && nonUserSources.length > 0  || 
    (body.imageSources && body.imageSources.length > 0)) {
    //need to ensure we extract the key, so far I have seen all ds start with s3:// but can_access_object table has it without 
    const ds_with_keys = nonUserSources.map(ds => ({ ...ds, id: extractKey(ds.id) }));
    const image_ds_keys = body.imageSources ? body.imageSources.map(ds =>  ({ ...ds, id: extractKey(ds.id) })) : [];
    console.log("IMAGE: ds_with_keys", image_ds_keys);
    if (!await canReadDataSources(params.accessToken, [...ds_with_keys, ...image_ds_keys])) {
        throw new Error("Unauthorized data source access.");
    }
}
```

**Ownership Logic**: Files under `{userId}/` path are automatically authorized (user owns them)
**External Authorization**: Non-owned files require explicit permission check via `canReadDataSources()`
**Key Extraction**: Removes S3 protocol prefix (`s3://`) for permission table lookup
**Image Authorization**: Includes images in authorization check
**Critical Security**: Throws error on unauthorized access - becomes HTTP 401 in router

### **Phase 4: Return Authorized Data Sources (Line 417)**

```javascript
return dataSources;
```

**Return**: Only the authorized, resolved data sources (excludes conversation sources and images)
**Usage**: Router continues with these validated data sources for LLM processing

---

## 🔍 NESTED FUNCTION ANALYSIS: Authorization & Translation Functions

### **1A. `canReadDataSources()` Authorization Check**

**File**: `amplify-lambda-js/common/permissions.js` (Lines 13-53)

```javascript
export const canReadDataSources = async (accessToken, dataSources) => {
    const accessLevels = {}
    dataSources.forEach(ds => {
        accessLevels[ds.id] = 'read'
    });

    const requestData = {
        data: {
            dataSources: accessLevels
        }
    }

    try {
        const response = await fetch(permissionsEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${accessToken}`
            },
            body: JSON.stringify(requestData)
        });

        const responseBody = await response.json();
        const statusCode = responseBody.statusCode || undefined;

        if (response.status !== 200 || statusCode !== 200) {
            console.error("User does not have access to datasources: " + response.status);
            return false;
        }
        else if(response.status === 200 && statusCode === 200) {
            return true;
        }
    }
    catch (e) {
        console.error("Error checking access on data sources: " + e);
        return false;
    }

    return false;
}
```

**API Endpoint**: `${API_BASE_URL}/utilities/can_access_objects` - calls `object-access` service
**Service**: `object-access` Lambda function with `@validated("can_access_objects")`
**Authorization Format**: Creates `{dataSourceId: 'read'}` mapping for permission check
**Response**: Boolean - `true` if authorized, `false` if denied or error
**Error Handling**: Returns `false` on network errors or service failures

### **1B. Backend Authorization Implementation: `/utilities/can_access_objects`**

**Service**: `object-access/object_access.py` (Lines 111-170)
**Table**: `OBJECT_ACCESS_DYNAMODB_TABLE` - stores per-object permissions

```python
@validated("can_access_objects")
def can_access_objects(event, context, current_user, name, data):
    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
    table = dynamodb.Table(table_name)
    data_sources = data["data"]["dataSources"]

    for object_id, access_type in data_sources.items():
        # Check if any permissions already exist for the object_id
        query_response = table.get_item(
            Key={"object_id": object_id, "principal_id": current_user}
        )
        item = query_response.get("Item")

        if not item:
            return {
                "statusCode": 403,
                "body": json.dumps({
                    "message": f"User does not have access to objectId.",
                    "objectId": object_id,
                    "accessType": access_type,
                })
            }

        permission_level = item.get("permission_level")
        policy = item.get("policy")
        if not is_sufficient_privilege(object_id, permission_level, policy, access_type):
            return {
                "statusCode": 403,
                "body": json.dumps({
                    "message": f"User does not have access to objectId.",
                    "objectId": object_id,
                    "accessType": access_type,
                })
            }

    return {"statusCode": 200, "body": "User has access to the object(s)."}
```

**Authorization Logic**:
1. **Lookup**: Query `OBJECT_ACCESS_DYNAMODB_TABLE` for `{object_id, principal_id}` combination
2. **Existence Check**: Return 403 if no permission record exists
3. **Permission Validation**: Verify `permission_level` and `policy` meet `access_type` requirements
4. **Response**: 200 if all objects authorized, 403 on first unauthorized object

**Permission Model**:
- **object_id**: Data source identifier (file path without S3 protocol)
- **principal_id**: User ID requesting access
- **permission_level**: Access level (read, write, admin)
- **policy**: Additional access restrictions/conditions

### **2A. `translateUserDataSourcesToHashDataSources()` - File Deduplication System**

**Function**: Lines 608-659 in `datasources.js`

```javascript
export const translateUserDataSourcesToHashDataSources = async (params, body, dataSources) => {
    const toResolve = dataSources ? dataSources.filter(ds => !isImage(ds)) : [];
    if (toResolve.length === 0) return [];
    dataSources = await resolveDataSourceAliases(params, body, toResolve);

    const translated = await Promise.all(dataSources.map(async (ds) => {
        let key = ds.id;

        try {
            if (key.startsWith("s3://")) {
                key = extractKey(key);
            }

            // Check the hash keys cache
            const cached = hashDataSourcesCache.get(key);
            if (cached) {
                return cached;
            }

            const command = new GetItemCommand({
                TableName: hashFilesTableName, // HASH_FILES_DYNAMO_TABLE
                Key: {
                    id: {S: key}
                }
            });

            const {Item} = await dynamodbClient.send(command);

            if (Item) {
                const item = unmarshall(Item);
                const result = {
                    ...ds,
                    metadata: {...ds.metadata, userDataSourceId: ds.id},
                    id: "s3://" + item.textLocationKey};
                hashDataSourcesCache.set(key, result);
                return result;
            } else {
                hashDataSourcesCache.set(key, ds);
                return ds; // No item found with the given ID
            }
        } catch (e) {
            console.log(e);
            return ds;
        }
    }));

    return translated.filter((ds) => ds != null);
}
```

**Purpose**: Implements file deduplication system using content hashes
**Process**:
1. **Tag Resolution**: `resolveDataSourceAliases()` - converts `tag://xyz` to actual data sources
2. **Hash Lookup**: Query `HASH_FILES_DYNAMO_TABLE` for duplicate detection
3. **ID Translation**: User file ID → Global hash-based file ID
4. **Metadata Preservation**: Stores original user ID in `metadata.userDataSourceId`
5. **Caching**: LRU cache for performance optimization

**Deduplication Benefits**:
- **Storage Efficiency**: Multiple users uploading same file → single storage copy
- **Processing Efficiency**: RAG indexing done once per unique file
- **Cost Optimization**: Reduces storage and processing costs

### **2B. `getDataSourcesInConversation()` - Conversation History Integration**

**Function**: Lines 33-44 in `datasources.js`

```javascript
export const getDataSourcesInConversation = (chatBody, includeCurrentMessage = true) => {
    if (chatBody && chatBody.messages) {
        const base = (includeCurrentMessage ? chatBody.messages : chatBody.messages.slice(0, -1))

        return base
            .filter(m => {
                return m.data && m.data.dataSources 
            }).flatMap(m => m.data.dataSources).filter(ds => !isImage(ds))
    }

    return [];
}
```

**Purpose**: Extracts all data sources referenced throughout conversation history
**Logic**: Scans all messages for `message.data.dataSources` arrays
**Exclusions**: Filters out image data sources (handled separately)
**Usage**: Enables RAG context from files mentioned earlier in conversation

---

## 🎯 DEEP DIVE #3 ANALYSIS COMPLETE

### **Summary: `resolveDataSources()` Function Analysis**

✅ **Main Function**: 46 lines analyzed across 4 distinct phases
✅ **Authorization System**: Complete object-access service integration documented
✅ **File Deduplication**: Hash-based system for storage optimization
✅ **Security Model**: Multi-layer permission checking with DynamoDB backing
✅ **Image Handling**: Separate processing pipeline for image data sources

### **Key Architecture Insights:**

1. **Security-First Design**: Multiple authorization layers prevent unauthorized data access
2. **File Deduplication System**: Hash-based storage reduces costs and improves performance
3. **Conversation Context**: Automatic integration of data sources from conversation history
4. **Performance Optimization**: LRU caching and efficient DynamoDB queries
5. **Error Handling**: Graceful fallbacks ensure system availability
6. **Image Separation**: Different processing pipelines for images vs documents

### **Critical Security Features:**
- **Ownership-Based Authorization**: Files under user path automatically authorized
- **Explicit Permission Checks**: Shared files require DynamoDB permission records
- **Service Integration**: Dedicated `object-access` service for granular permissions
- **Error Responses**: Unauthorized access results in HTTP 401 response
- **Token Validation**: Bearer token required for all authorization checks

### **Data Flow Architecture:**
1. **Frontend Request** → Data sources included in chat request
2. **Image Separation** → Images processed separately from documents  
3. **Hash Translation** → User file IDs converted to global hash IDs
4. **Permission Check** → Non-owned files validated via object-access service
5. **Authorization Response** → Authorized data sources returned for LLM processing

---

## 📋 REMAINING DEEP DIVE TARGETS (Updated)

### **🎯 Priority 1: Core Infrastructure**
1. ✅ **`getUserAvailableModels()`** - Model permission system - **COMPLETED**
2. ✅ **`resolveDataSources()`** - Data source authorization - **COMPLETED**  
3. **`createRequestState()`** - Request tracking system (Line 189 in router)
4. **LLM Class** - AI model wrapper interface (Lines 191-194 in router)

### **🎯 Priority 2: Processing Functions**
5. **`getChatFn()`** - Model-specific chat function provider
6. **`getModelByType()`** - Model type resolution
7. **`sendStateEventToStream()`** - Client streaming updates
8. **`trace()` and `saveTrace()`** - Request tracing system

### **🎯 Priority 3: Assistant System Deep Dive**
9. **Assistant Handler Interface** - Standard assistant execution pattern
10. **`defaultAssistant.handler()`** - Core chat processing logic (Lines 32-58)
11. **`mapReduceAssistant`** - Large document processing assistant
12. **`getDataSourcesByUse()`** - Data source processing and filtering

*Analysis Status: ✅ DEEP DIVE #3 COMPLETE - `resolveDataSources()` fully analyzed*

---

## 🎯 DEEP DIVE #4: `createRequestState()` - Request Tracking & Cancellation System

### **Function Overview**
- **File**: `amplify-lambda-js/requests/requestState.js`
- **Lines**: 74-76 (3 lines + supporting infrastructure)
- **Purpose**: Critical request tracking system enabling real-time cancellation and monitoring of long-running AI conversations
- **Called From**: Router line 189: `await createRequestState(params.user, requestId);`
- **Import**: Line 8 of router.js: `import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js"`

### **Function Signature & Entry Point (Lines 74-76)**

```javascript
export const createRequestState = async (user, requestId) => {
    return await updateKillswitch(user, requestId, false);
}
```

**Simple Wrapper**: Delegates to `updateKillswitch()` with `killswitch = false` (request is active)
**Parameters**: 
- `user`: User ID for request ownership
- `requestId`: Unique identifier for the specific chat request
- **Return**: Boolean indicating success of state creation

### **Core Implementation: `updateKillswitch()` Function (Lines 107-135)**

```javascript
export const updateKillswitch = async (user, requestId, killswitch) => {
    if (!requestsTable) {
        logger.error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
    }

    // Calculate TTL: current time + 1 day (86400 seconds)
    const ttl = Math.floor(Date.now() / 1000) + 86400;

    const command = new PutItemCommand({
        TableName: requestsTable,
        Item: {
            user: {S: user},
            requestId: {S: requestId},
            exit: {BOOL: killswitch},
            lastUpdatedTime: {N: "" + new Date().getTime()},
            ttl: {N: "" + ttl}
        }
    });

    logger.debug("Updating request state.");
    const response = await dynamodbClient.send(command);
    return true;
}
```

**Purpose**: Creates/updates request state record in DynamoDB with TTL expiration
**Database**: `REQUEST_STATE_DYNAMO_TABLE` with composite key `{user, requestId}`
**TTL Management**: 24-hour auto-expiration prevents database bloat
**State Fields**:
- `exit: BOOL` - Killswitch flag (false = active, true = cancelled)
- `lastUpdatedTime: N` - Timestamp for tracking/debugging
- `ttl: N` - DynamoDB TTL for automatic cleanup

---

## 🔍 REQUEST STATE LIFECYCLE & USAGE PATTERNS

### **Phase 1: Request Initialization (Router Line 189)**

**Creation Context**: Called immediately after X-Ray segment creation, before assistant selection
```javascript
const initSegment = segment.addNewSubsegment('chat-js.router.init');
await createRequestState(params.user, requestId);
```

**Purpose**: Establishes tracking record enabling cancellation capability throughout request lifecycle

### **Phase 2: Killswitch Activation (Router Lines 49-70)**

**Frontend API**: Clients can send killswitch requests via chat endpoint
```javascript
// Client request format
{
    "killSwitch": {
        "requestId": "unique-request-id",
        "value": true  // true = cancel, false = reactivate
    }
}
```

**Router Processing**:
```javascript
} else if(params.body.killSwitch) {
    try {
        const {requestId, value} = params.body.killSwitch;
        await updateKillswitch(params.user, requestId, value);
        returnResponse(responseStream, {
            statusCode: 200,
            body: {status: "OK"}
        });
    } catch (e) {
        return returnResponse(responseStream, {
            statusCode: 400,
            body: {error: "Invalid killswitch request"}
        });
    }
}
```

**User Experience**: Frontend "Cancel" button → killswitch request → immediate response → background cancellation

### **Phase 3: Continuous Monitoring & Cancellation Checks**

### **3A. Core Monitoring Function: `isKilled()` (Lines 149-178)**

```javascript
export const isKilled = async (user, responseStream, chatRequest) => {
    if (chatRequest && chatRequest.options) {
        const requestId = chatRequest.options.requestId;

        if (requestId) {
            // Check local cache first (performance optimization)
            const key = getKillSwitchKey(user, requestId);
            if (killedCache.get(key)) {
                logger.info("Killswitch triggered, exiting.");
                return true;
            }

            // Check DynamoDB state
            const doExit = await shouldKill(user, requestId);
            if (doExit) {
                try {
                    killedCache.set(key, true);
                    await deleteRequestState(user, requestId);
                } catch (e) {
                    logger.error("Error deleting request state: " + e);
                }

                responseStream.end();
                logger.info("Killswitch triggered, exiting.");
                return true;
            }
        }
    }
    return false;
}
```

**Performance Features**:
- **LRU Cache**: `killedCache` (10 entries) prevents repeated DynamoDB queries
- **Immediate Response**: Cached kills return instantly
- **Stream Termination**: `responseStream.end()` stops output immediately
- **Cleanup**: Deletes DynamoDB record after successful cancellation

### **3B. DynamoDB State Check: `shouldKill()` (Lines 44-72)**

```javascript
export const shouldKill = async (user, requestId) => {
    const command = new GetItemCommand({
        TableName: requestsTable,
        Key: {
            user: {S: user},
            requestId: {S: requestId}
        }
    });

    const response = await dynamodbClient.send(command);

    if (!response.Item) {
        logger.debug("Request state not found, assuming no limits...");
        return false;
    }

    let killswitch = response.Item.exit.BOOL;
    logger.debug(`Killswitch state is ${killswitch ? "kill" : "continue"}.`);

    return killswitch;
}
```

**Logic**: 
- **No Record**: Default to continue (false) - handles cleanup/expiration cases
- **Record Found**: Return `exit` boolean value
- **Performance**: Single DynamoDB read operation

### **Phase 4: System-Wide Integration Points**

**Usage Locations** (from grep analysis):

#### **4A. Workflow Engine Integration** (`workflow/workflow.js`)
- **6 cancellation checkpoints** throughout multi-step workflows
- **Example**: `if(await isKilled(params.account.user, responseStream, body)){ return; }`
- **Purpose**: Prevents runaway workflows, enables mid-execution cancellation

#### **4B. CSV Processing Integration** (`assistants/csv.js`)
- **Batch Operation Cancellation**: Stops large CSV processing operations
- **Resource Cleanup**: `await limiter.stop()` - stops rate limiters
- **Purpose**: Prevents long-running data processing from consuming resources

#### **4C. Sequential Chat Integration** (`sequentialChat.js`)
- **Multi-Model Cancellation**: Stops processing across multiple AI models
- **Purpose**: Enables cancellation during parallel model execution

#### **4D. State Machine Integration** (`statemachine/states.js`)
- **Assistant State Tracking**: `context.assistantKilled = true`
- **Purpose**: Complex assistant workflows can be cancelled mid-execution

---

## 🔄 REQUEST LIFECYCLE MANAGEMENT

### **Cleanup & Resource Management**

#### **`deleteRequestState()` Function (Lines 78-105)**
```javascript
export const deleteRequestState = async (user, requestId) => {
    try {
        const command = new DeleteItemCommand({
            TableName: requestsTable,
            Key: {
                user: {S: user},
                requestId: {S: requestId}
            }
        });

        await dynamodbClient.send(command);
        return true;
    } catch (e) {
        return false;
    }
}
```

**Usage**: Called after successful cancellation or request completion
**Purpose**: Immediate cleanup rather than waiting for TTL expiration

#### **Local Caching System (Lines 138-147)**
```javascript
const killedCache = lru(10, 0, false);

export const localKill = (user, requestId) => {
    const key = getKillSwitchKey(user, requestId);
    killedCache.set(key, true);
}

function getKillSwitchKey(user, requestId) {
    return user + "__" + requestId;
}
```

**Cache Strategy**: 
- **Size**: 10 most recent killed requests
- **Format**: `"user__requestId"` composite key
- **TTL**: No expiration (0) - relies on LRU eviction
- **Purpose**: Prevents repeated DynamoDB queries for already-cancelled requests

---

## 🎯 DEEP DIVE #4 ANALYSIS COMPLETE

### **Summary: `createRequestState()` Function Analysis**

✅ **Main Function**: 3-line wrapper with extensive supporting infrastructure
✅ **Request Tracking**: Complete DynamoDB-based state management system
✅ **Cancellation System**: Real-time killswitch with performance optimizations
✅ **System Integration**: Used across 6+ major processing components
✅ **Resource Management**: Automatic cleanup with TTL + manual deletion

### **Key Architecture Insights:**

1. **Real-Time Cancellation**: Users can cancel long-running AI operations via frontend
2. **Performance Optimization**: LRU cache prevents repeated DynamoDB reads  
3. **System-Wide Integration**: Cancellation checkpoints throughout processing pipeline
4. **Resource Management**: TTL + manual cleanup prevents database bloat
5. **Graceful Termination**: Stream ending + resource cleanup on cancellation
6. **User Experience**: Immediate response to cancel requests with background processing

### **Critical System Benefits:**
- **Cost Control**: Prevents runaway operations from consuming compute resources
- **User Experience**: Responsive cancellation for long AI conversations
- **Resource Management**: Automatic cleanup prevents database growth
- **System Reliability**: Graceful handling of cancelled operations
- **Performance**: Cached kill states reduce DynamoDB load

### **Technical Implementation Highlights:**
- **Composite Keys**: `{user, requestId}` ensures user-specific isolation
- **TTL Management**: 24-hour automatic expiration prevents data accumulation
- **Cache Strategy**: 10-entry LRU cache for frequently checked kill states
- **Error Handling**: Graceful fallbacks when state records are missing
- **Stream Integration**: Direct response stream termination on cancellation

### **Integration Points:**
1. **Frontend**: Cancel button → killswitch API call
2. **Router**: Request initialization + killswitch endpoint
3. **Workflows**: Multi-step process cancellation checkpoints
4. **Assistants**: Long-running operation cancellation (CSV, AI processing)
5. **Chat Controllers**: Multi-model operation cancellation
6. **State Machines**: Complex assistant workflow cancellation

---

## 📋 REMAINING DEEP DIVE TARGETS (Updated)

### **🎯 Priority 1: Core Infrastructure**
1. ✅ **`getUserAvailableModels()`** - Model permission system - **COMPLETED**
2. ✅ **`resolveDataSources()`** - Data source authorization - **COMPLETED**  
3. ✅ **`createRequestState()`** - Request tracking system - **COMPLETED**
4. **LLM Class** - AI model wrapper interface (Lines 191-194 in router)

### **🎯 Priority 2: Processing Functions**
5. **`getChatFn()`** - Model-specific chat function provider
6. **`getModelByType()`** - Model type resolution
7. **`sendStateEventToStream()`** - Client streaming updates
8. **`trace()` and `saveTrace()`** - Request tracing system

### **🎯 Priority 3: Assistant System Deep Dive**
9. **Assistant Handler Interface** - Standard assistant execution pattern
10. **`defaultAssistant.handler()`** - Core chat processing logic (Lines 32-58)
11. **`mapReduceAssistant`** - Large document processing assistant
12. **`getDataSourcesByUse()`** - Data source processing and filtering

*Analysis Status: ✅ DEEP DIVE #4 COMPLETE - `createRequestState()` request tracking system fully analyzed*

---

## 🎯 DEEP DIVE #5: `defaultAssistant.handler()` + LLM Integration Analysis

### **Function Overview**
- **File**: `amplify-lambda-js/assistants/assistants.js`
- **Lines**: 32-58 (27 lines)
- **Purpose**: Core chat processing logic that determines execution path and interfaces with LLM system
- **Called From**: Router line 205: `await assistant.handler(llm, assistantParams, body, dataSources, responseStream)`
- **Critical**: This is where the main AI conversation processing begins - the heart of the chat system

### **Function Signature & Entry Point (Lines 32-58)**

```javascript
handler: async (llm, params, body, ds, responseStream) => {
    // already ensures model has been mapped to our backend version in router
    const model = (body.options && body.options.model) ? body.options.model : params.model;

    logger.debug("Using model: ", model);

    const {dataSources} = await getDataSourcesByUse(params, body, ds);

    const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
    const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCount(ds, model), 0);
    const aboveLimit = requiredTokens >= limit;

    logger.debug(`Model: ${model.id}, tokenLimit: ${model.inputContextWindow}`)
    logger.debug(`RAG Only: ${body.options.ragOnly}, dataSources: ${dataSources.length}`)
    logger.debug(`Required tokens: ${requiredTokens}, limit: ${limit}, aboveLimit: ${aboveLimit}`);

    if (params.blockTerminator) {
        body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
    }

    if (!body.options.ragOnly && aboveLimit){
        return mapReduceAssistant.handler(llm, params, body, dataSources, responseStream);
    } else {
        return llm.prompt(body, dataSources);
    }
}
```

**Parameters Analysis:**
- `llm`: LLM wrapper instance with chat functions and streaming capabilities
- `params`: Assistant parameters with account context, model configs, and request tracking
- `body`: Request body with messages, options, and conversation data
- `ds`: Authorized data sources array from `resolveDataSources()`
- `responseStream`: Response stream for real-time client communication

---

## 📊 EXECUTION FLOW ANALYSIS

### **Phase 1: Model Resolution & Data Source Processing (Lines 34-39)**

**Model Selection Logic (Lines 34-37)**:
```javascript
const model = (body.options && body.options.model) ? body.options.model : params.model;
logger.debug("Using model: ", model);
```
- **Priority**: Request-specific model override takes precedence over assistant defaults
- **Fallback**: Uses `params.model` (verified in router from `getUserAvailableModels()`)
- **Result**: Final model configuration for this conversation

**Data Source Processing (Line 39)**:
```javascript
const {dataSources} = await getDataSourcesByUse(params, body, ds);
```
- **Function Call**: `getDataSourcesByUse()` - **CRITICAL LLM INTEGRATION POINT**
- **Purpose**: Processes data sources for RAG integration and context window management
- **Input**: Authorized data sources from router's `resolveDataSources()`
- **Output**: Processed data sources ready for LLM consumption

### **Phase 2: Context Window Analysis & Token Calculation (Lines 41-47)**

**Token Limit Calculation (Line 41)**:
```javascript
const limit = 0.9 * (model.inputContextWindow - (body.max_tokens || 1000));
```
- **Formula**: 90% of available context window minus output buffer
- **Safety Buffer**: 10% reserved for prompt formatting and unexpected overhead
- **Output Reserve**: `max_tokens` (default 1000) reserved for AI response
- **Example**: GPT-4 (128k context) → 0.9 * (128000 - 1000) = 114,300 tokens available

**Required Tokens Assessment (Line 42)**:
```javascript
const requiredTokens = [...dataSources, ...(body.imageSources || [])].reduce((acc, ds) => acc + getTokenCount(ds, model), 0);
```
- **Calculation**: Sums all data source token requirements
- **Includes**: Both document data sources and image sources
- **Function**: `getTokenCount(ds, model)` - model-specific token counting
- **Purpose**: Determines if all data fits in context window

**Context Window Decision (Line 43)**:
```javascript
const aboveLimit = requiredTokens >= limit;
```
- **Critical Decision Point**: Determines execution path
- **True**: Data sources exceed context window → use map-reduce processing
- **False**: Data sources fit → use standard LLM processing

**Debug Logging (Lines 45-47)**:
- Model identification and token limits
- RAG configuration status
- Token calculations for debugging

### **Phase 3: Block Terminator Integration (Lines 49-51)**

```javascript
if (params.blockTerminator) {
    body = {...body, options: {...body.options, blockTerminator: params.blockTerminator}};
}
```
- **Purpose**: Adds assistant-specific output termination patterns
- **Usage**: Automatically stops AI output at specific markers (e.g., `</task>`, `[END]`)
- **Application**: Useful for structured outputs and controlled responses

### **Phase 4: Execution Path Decision (Lines 53-57)**

**Large Document Path (Lines 53-54)**:
```javascript
if (!body.options.ragOnly && aboveLimit){
    return mapReduceAssistant.handler(llm, params, body, dataSources, responseStream);
}
```
- **Condition**: Not RAG-only mode AND data exceeds context window
- **Action**: Delegates to `mapReduceAssistant` for workflow-based processing
- **Result**: Multi-step document processing with map-reduce pattern

**Standard LLM Path (Lines 55-56)**:
```javascript
} else {
    return llm.prompt(body, dataSources);
}
```
- **Condition**: RAG-only mode OR data fits in context window
- **Action**: Direct LLM processing with integrated RAG
- **Result**: Single LLM call with optimized context

---

## 🔍 CRITICAL NESTED FUNCTION ANALYSIS

### **5A. `getDataSourcesByUse()` - Data Source Processing Engine**

**File**: `amplify-lambda-js/datasource/datasources.js` (Lines 136-253)

```javascript
export const getDataSourcesByUse = async (params, body, dataSources) => {
    const originalDs = dataSources;
    let ragDataSources = [];
    let conversationDataSources = [];

    if (!params.options.skipRag && !params.options.ragOnly) { // RAG + document processing
        const contextualDs = dataSources.filter(ds => !ds.metadata?.ragOnly);
        const ragOnlyDs = dataSources.filter(ds => ds.metadata?.ragOnly);
        
        dataSources = contextualDs;
        ragDataSources = ragOnlyDs;
    } else if (!params.options.skipRag && params.options.ragOnly) { // RAG only
        ragDataSources = dataSources;
        dataSources = [];
    } else if (params.options.skipRag && !params.options.skipDocumentCache) { // Document cache only
        conversationDataSources = dataSources;
        dataSources = [];
    } else { // Skip all data source processing
        dataSources = [];
    }

    return {
        dataSources,           // Documents inserted into context
        ragDataSources,        // Documents searched with RAG
        conversationDataSources, // Documents from conversation cache
        originalDs             // Original unprocessed data sources
    };
}
```

**Purpose**: Categorizes data sources into different processing pipelines based on request options
**Processing Modes**:
1. **Full Processing**: `dataSources` (contextual) + `ragDataSources` (search-based)
2. **RAG Only**: All data sources become search-based RAG queries
3. **Document Cache**: Uses conversation-cached document content
4. **Skip All**: No data source processing (conversation only)

**Integration with LLM**: This categorization determines how data flows through the LLM processing pipeline

### **5B. `getTokenCount()` - Model-Specific Token Counting**

**File**: `amplify-lambda-js/assistants/assistants.js` (Lines 180-193)

```javascript
const getTokenCount = (dataSource, model) => {
    if (dataSource.metadata && dataSource.metadata.totalTokens) {
        const totalTokens = dataSource.metadata.totalTokens;
        if (isImage(dataSource)) {
            return isOpenAIModel(model.id) ? totalTokens.gpt : 
                 model.id.includes("anthropic") ? totalTokens.claude : 1000;
        }
        if (!dataSource.metadata.ragOnly) return totalTokens;
    }
    else if(dataSource.metadata && dataSource.metadata.ragOnly){
        return 0;
    }
    return 1000;
}
```

**Purpose**: Calculates token usage for context window management
**Model-Specific Logic**: 
- **Images**: Different token costs per model (OpenAI vs Claude vs others)
- **Documents**: Uses pre-calculated `totalTokens` from metadata
- **RAG-Only**: Returns 0 (won't be inserted directly into context)
- **Fallback**: 1000 tokens for unknown data sources

---

## 🚀 EXECUTION PATH #1: `mapReduceAssistant.handler()` - Large Document Processing

### **When Triggered**: `!ragOnly && aboveLimit` (data exceeds context window)

**File**: `amplify-lambda-js/assistants/mapReduceAssistant.js` (Lines 21-71)

```javascript
handler: async (llm, params, body, dataSources, responseStream) => {
    const task = body.messages.slice(-1)[0].content;

    const workflow = {
        resultKey: "answer",
        steps: [
            {
                prompt: "__use_body__",
                input: dataSources.map(ds => ds.id),
                outputTo: "parts"
            },
            {
                statusMessage: "Condensing my answer...",
                input: ["parts"],
                reduce: `Above are the parts of the response to the task below. 
--------------
${task}
--------------             
Combine these parts into one cohesive answer.
Try to preserve the formatting from the best part. 
Make sure and preserve as much information as possible while still making the answer cohesive.

If the user refers to documents, information, data sources, etc., the parts above are your 
access to that information and you should use them to provide the best answer possible.`,
                outputTo: "answer"
            }
        ]
    }

    console.log("Starting local workflow....");

    const response = await executeWorkflow({
        workflow,
        body,
        params,
        chatFn: llm.chatFn,
        chatRequest: body,
        dataSources,
        responseStream,
        initialState: {}
    });

    console.log("Local workflow finished.");
    responseStream.end();
}
```

**Map-Reduce Pattern**:
1. **Map Phase**: Process each data source separately with original user prompt
2. **Reduce Phase**: Combine all responses into single coherent answer
3. **Workflow Engine**: Uses `executeWorkflow()` for orchestrated multi-step processing

**LLM Usage**: 
- **Multiple LLM Calls**: One per data source + final combination call
- **Context Management**: Each call fits within context window limits
- **Streaming**: Real-time updates through workflow status messages

---

## 🎯 EXECUTION PATH #2: `llm.prompt()` - Standard LLM Processing

### **When Triggered**: `ragOnly || !aboveLimit` (RAG-only mode or data fits)

**Function**: `LLM.prompt()` in `amplify-lambda-js/common/llm.js` (Lines 182-199)

```javascript
async prompt(body, dataSources = [], targetStream = this.responseStream) {
    const updatedParams = {
        ...this.params,
        model: (body.options && body.options.model) || (this.params.options && this.params.options.model),
        options: {
            ...this.params.options,
            ...body.options
        }
    };

    return chatWithDataStateless(
        updatedParams,
        this.chatFn,
        {...this.defaultBody, ...body},
        dataSources,
        targetStream);
}
```

**Purpose**: Entry point to comprehensive chat processing with RAG integration
**Parameters Merging**: Combines LLM instance params with request-specific options
**Delegation**: Calls `chatWithDataStateless()` for full processing pipeline

---

## 🔄 COMPREHENSIVE LLM PROCESSING: `chatWithDataStateless()` Analysis

**File**: `amplify-lambda-js/common/chatWithData.js` (Lines 147-504)

### **Phase 1: Data Source Processing & RAG Integration (Lines 147-226)**

**Data Source Categorization (Line 158)**:
```javascript
const allSources = await getDataSourcesByUse(params, chatRequestOrig, dataSources);
```
**Multi-Pipeline Processing**:
- `allSources.dataSources`: Direct context insertion
- `allSources.ragDataSources`: RAG-based search and retrieval  
- `allSources.conversationDataSources`: Conversation cache integration

**RAG Processing (Lines 199-226)**:
```javascript
const {messages: ragContextMsgs, sources} = (ragDataSources.length > 0) ?
    await getContextMessages(params, chatRequestOrig, ragDataSources) :
    {messages: [], sources: []};
```
- **Function**: `getContextMessages()` - RAG search and retrieval engine
- **Output**: Contextual messages + source metadata
- **Streaming**: Status updates sent to client during RAG processing

### **Phase 2: Message Construction & Token Management (Lines 229-278)**

**Safe Message Processing (Lines 229-244)**:
```javascript
const safeMessages = [
    ...chatRequestOrig.messages.map(m => {
        return {role: m.role, content: m.content}
    })
];

const chatRequest = {
    ...chatRequestOrig,
    messages: [
        ...safeMessages.slice(0, -1),
        ...ragContextMsgs,
        ...safeMessages.slice(-1)
    ]
};
```
**Security**: Strips non-standard message attributes to prevent injection
**RAG Integration**: Inserts RAG results between conversation history and current prompt

**Token Limit Management (Lines 263-268)**:
```javascript
let msgTokens = tokenCounter.countMessageTokens(chatRequest.messages);
const maxTokensForMessages = model.inputContextWindow - tokenLimitBuffer - minTokensForContext
if(msgTokens > maxTokensForMessages) {
    chatRequest.messages = fitMessagesInTokenLimit(chatRequest.messages, maxTokensForMessages);
}
```
**Dynamic Truncation**: Automatically trims conversation history to fit context window
**Smart Preservation**: `fitMessagesInTokenLimit()` keeps most recent and important messages

### **Phase 3: Document Context Processing (Lines 346-453)**

**Context Generation (Lines 373-443)**:
```javascript
contexts = (await Promise.all([
    ...dataSources.map(async dataSource => {
        const results = await getContexts(contextResolverEnv, dataSource, maxTokens, options);
        return results.map(result => ({...result, type: DOCUMENT_CONTEXT}));
    }), 
    ...conversationDataSources.map(async dataSource => {
        const results = await getContexts(contextResolverEnv, dataSource, maxTokens, options, true);
        return results.map(result => ({...result, type: DOCUMENT_CONTEXT_CACHE}));
    }) 
]))
.flat()
.filter(context => context !== null)
```

**Document Processing**: 
- **Chunking**: `getContexts()` breaks documents into context-window-sized chunks
- **Token Awareness**: Each chunk respects `maxTokens` limit
- **Type Tagging**: Differentiates between live documents and cached content

**Context Merging Optimization (Lines 466-474)**:
```javascript
if (updatedContexts.length > 1) {
    const totalDsTokens = contexts.reduce((acc, context) => acc + (context.tokens || 1000), 0);
    if (totalDsTokens <= maxTokens) {
        logger.debug("Merging contexts into one: ", updatedContexts);
        const mergedContext = contexts.map((c, index) => `DataSource ${index + 1}: \n\n${c.context}`).join("\n\n");
        updatedContexts = [{id: 0, context: mergedContext, contexts: updatedContexts}];
    }
}
```
**Performance Optimization**: Merges multiple small contexts into single LLM call when possible

### **Phase 4: Event Transformation & Usage Tracking (Lines 296-340)**

**Provider-Specific Streaming (Lines 296-340)**:
```javascript
const eventTransformer = (event) => {
    if (isOpenAIModel(model.id)) {
        const usage = openaiUsageTransform(event);
        if (usage) {
            recordUsage(account, requestId, model, usage.prompt_tokens, usage.completion_tokens, 
                        usage.prompt_tokens_details?.cached_tokens ?? 0, {...details});
        }
        result = openAiTransform(event, responseStream);  
    } else if (model.provider === 'Bedrock') {
        const usage = bedrockTokenUsageTransform(event);
        if (usage) {
            recordUsage(account, requestId, model, usage.inputTokens, usage.outputTokens, 0, details);
        }
        result = bedrockConverseTransform(event, responseStream);
    } else if (isGeminiModel(model.id)) {            
        result = geminiTransform(event, responseStream);
        const usage = geminiUsageTransform(event);
        if (usage) {
            recordUsage(account, requestId, model, usage.prompt_tokens, usage.completion_tokens, 
                        usage.prompt_tokens_details?.cached_tokens ?? 0, {...details});
        }
    }
    return result;
}
```

**Multi-Provider Support**:
- **OpenAI Models**: GPT-4, GPT-3.5, O1 models with usage tracking
- **Bedrock Models**: AWS Bedrock with different token format
- **Gemini Models**: Google Gemini with specific streaming format

**Usage Tracking**: Real-time token usage recording for billing and analytics

### **Phase 5: Chat Controller Execution (Lines 495-496)**

**Controller Selection (Lines 50-53)**:
```javascript
const chooseController = ({chatFn, chatRequest, dataSources}) => {
    return sequentialChat;
    //return parallelChat;
}
```
**Current Implementation**: Uses `sequentialChat` (parallel option commented out)

**Final Execution**:
```javascript
const controller = chooseController(chatContext);
await controller(chatContext);
```
**Delegation**: Passes complete context to chat controller for final LLM interaction

---

## 🎬 FINAL LLM EXECUTION: `sequentialChat.handleChat()` Analysis

**File**: `amplify-lambda-js/common/chat/controllers/sequentialChat.js` (Lines 17-165)

### **Multi-Context Processing Loop (Lines 54-132)**

**Stream Multiplexing Setup (Lines 26-32)**:
```javascript
const multiplexer = new StreamMultiplexer(responseStream);
const user = account.user;
const requestId = chatRequest.options.requestId;
let llmResponse = '';

sendSourceMetadata(multiplexer, metaData);
```
**Purpose**: Coordinates multiple LLM streams into single client response

**Context Processing Loop (Lines 54-132)**:
```javascript
for (const [index, context] of contexts.entries()) {
    if ((await isKilled(user, responseStream, chatRequest))) {
        return;
    }

    let messages = [...chatRequest.messages];
    messages = addContextMessage(messages, context);

    const requestWithData = {
        ...chatRequest,
        messages: messages
    }

    const streamReceiver = new PassThrough();
    streamReceiver.on('data', (chunk) => {
        // Parse streaming response and accumulate llmResponse
        const chunkStr = chunk.toString();
        const jsonStrings = chunkStr.split('\n').filter(str => str.startsWith('data: ')).map(str => str.replace('data: ', ''));

        for (const jsonStr of jsonStrings) {
            if (jsonStr === '[DONE]') continue;
            
            try {
                const chunkObj = JSON.parse(jsonStr);
                if (chunkObj?.d?.delta?.text) { // Bedrock format
                    llmResponse += chunkObj.d.delta.text;              
                } else if (chunkObj?.choices?.[0]?.delta?.content) { // OpenAI format
                    llmResponse += chunkObj.choices[0].delta.content;
                } else if (chunkObj?.choices?.[0]?.message?.content) { // O1 format
                    llmResponse += chunkObj.choices[0].message.content;
                } else if (chunkObj?.type === "response.output_text.delta" && chunkObj.delta) { // OpenAI API format
                    llmResponse += chunkObj.delta;
                }
            } catch (e) {
                logger.debug(`Warning: Error parsing chunk: ${e.message}`);
            }
        }
    });

    multiplexer.addSource(streamReceiver, context.id, eventTransformer);
    
    await chatFn(requestWithData, streamReceiver);
    await multiplexer.waitForAllSourcesToEnd();
}
```

**Key Processing Elements**:
1. **Cancellation Checks**: `isKilled()` allows mid-processing cancellation
2. **Context Integration**: Each context added as separate message
3. **Stream Parsing**: Real-time response parsing for multiple model formats
4. **Response Accumulation**: Complete LLM response captured for analysis
5. **Multiplexing**: Multiple context responses combined into single stream

### **Response Analysis & Tracking (Lines 142-164)**

**Usage Analytics (Lines 142-143)**:
```javascript
trace(requestId, isDirectResponseToUser ? ["LLM Final User Response"] : ["LLM Amplify Prompted Response"], {data: llmResponse});
console.log("--llm response-- ", llmResponse);
```
**Purpose**: Request tracing and response logging for debugging/analytics

**Conversation Analysis (Lines 145-164)**:
```javascript
if (isDirectResponseToUser) {
    if (chatRequest.options.trackConversations) {
        const performCategoryAnalysis = !!chatRequest.options?.analysisCategories;
        
        analyzeAndRecordGroupAssistantConversation(
            chatRequest,
            llmResponse,
            account,
            performCategoryAnalysis
        ).catch(error => {
            logger.debug('Error in analyzeAndRecordGroupAssistantConversation:', error);
        });
    }
}
```
**Features**:
- **Conversation Tracking**: Stores conversation for future analysis
- **Category Analysis**: Optional content categorization
- **Group Assistant Analytics**: Tracks usage patterns and effectiveness

---

## 🎯 DEEP DIVE #5 ANALYSIS COMPLETE

### **Summary: Complete `defaultAssistant.handler()` + LLM Integration Analysis**

✅ **Handler Function**: 27 lines with complete execution path analysis
✅ **LLM Integration**: Full pipeline from handler → LLM → chat processing → streaming
✅ **Two Execution Paths**: Map-reduce workflow + standard chat processing both analyzed
✅ **Supporting Functions**: 8+ critical functions analyzed with complete data flow
✅ **Multi-Provider Support**: OpenAI, Bedrock, Gemini integration patterns documented
✅ **Real-time Processing**: Streaming, cancellation, usage tracking all mapped

### **Key Architecture Insights:**

1. **Intelligent Context Management**: Dynamic routing based on data size vs context window limits
2. **Multi-Modal Processing**: Different pipelines for large documents vs standard conversations  
3. **Real-Time Streaming**: Sophisticated multiplexing for multi-context responses
4. **Provider Abstraction**: Unified interface across different LLM providers
5. **Performance optimization**: Context merging, token management, and caching strategies
6. **Comprehensive Monitoring**: Usage tracking, conversation analysis, and request tracing

### **Complete LLM Usage Patterns Identified:**

#### **Path 1: Large Document Processing (`mapReduceAssistant`)**
- **Trigger**: Data sources exceed 90% of context window
- **Pattern**: Multi-step workflow with map-reduce processing
- **LLM Calls**: N+1 calls (one per data source + final combination)
- **Streaming**: Workflow status updates with final combined response

#### **Path 2: Standard Chat Processing (`llm.prompt`)**
- **Trigger**: Data fits in context window OR RAG-only mode
- **Pattern**: Single integrated LLM call with full context
- **Components**: RAG integration + document contexts + conversation history
- **Streaming**: Real-time token-by-token response with usage tracking

### **Critical LLM Integration Points:**

1. **`getDataSourcesByUse()`**: Data source categorization for different processing modes
2. **`chatWithDataStateless()`**: Core chat processing with RAG and context management  
3. **`sequentialChat()`**: Multi-context execution with streaming coordination
4. **Event Transformers**: Provider-specific streaming format handling
5. **Usage Tracking**: Real-time token counting and billing integration
6. **Cancellation System**: Mid-execution request cancellation capability

### **Performance & Optimization Features:**

- **Token Management**: Dynamic context window optimization
- **Context Merging**: Combining small contexts for efficient processing
- **Stream Multiplexing**: Coordinated multi-source streaming
- **LRU Caching**: Performance optimization for repeated operations
- **Conversation Analysis**: Background processing for analytics

### **Multi-Provider LLM Support:**

- **OpenAI**: GPT-4, GPT-3.5, O1 models with specific usage tracking
- **AWS Bedrock**: Claude, Titan models with Bedrock-specific formatting
- **Google Gemini**: Gemini models with Google-specific streaming
- **Unified Interface**: Single LLM class abstracts provider differences

---

## 📋 DEEP DIVE COMPLETION STATUS (Final Update)

### **🎯 Core Infrastructure - COMPLETED**
1. ✅ **`chooseAssistantForRequest()`** - Assistant routing system - **COMPLETED**
2. ✅ **`getUserAvailableModels()`** - Model permission system - **COMPLETED**
3. ✅ **`resolveDataSources()`** - Data source authorization - **COMPLETED**  
4. ✅ **`createRequestState()`** - Request tracking system - **COMPLETED**
5. ✅ **`defaultAssistant.handler()` + LLM Integration** - Complete chat processing pipeline - **COMPLETED**

### **🎯 Complete LLM Integration Analysis - COMPLETED**
✅ **Two Execution Paths**: Map-reduce workflow + standard processing
✅ **RAG Integration**: Complete search and retrieval processing
✅ **Context Management**: Token limits, context merging, message handling
✅ **Streaming Architecture**: Multi-provider streaming with usage tracking
✅ **Performance Features**: Caching, optimization, cancellation
✅ **Provider Support**: OpenAI, Bedrock, Gemini complete integration

*Analysis Status: ✅ ALL DEEP DIVES COMPLETE - Router.js main processing flow (lines 90-224) and complete LLM integration fully analyzed*