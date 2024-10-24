//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import express from 'express';
import cors from 'cors';
import {routeRequest} from "../router.js";
import {extractParams} from "../common/handlers.js";
 
 
const app = express();
const port = 8000; // Feel free to use any port
 
app.use(express.json({limit: '50mb'}));
app.use(express.urlencoded({limit: '50mb'}));
 
 
class SSEWrapper {
    constructor(res) {
        this.res = res;
        this.initialized = false;
    }
 
    write(data) {
        if (!this.initialized) {
            this.initialize();
        }
        this.res.write(data);
    }
 
    initialize() {
        this.res.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
        });
        this.initialized = true;
    }
 
    returnResponse = (response) => {
        if(!this.initialized) {
            this.res.writeHead(response.statusCode, {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            });
 
            this.initialized = true;
        }
        if(!this.res.writableEnded) {
            this.res.write(JSON.stringify(response.body));
            this.res.end();
        }
    }
 
    // Optionally, you can add a method to close the stream
    end() {
        if(!this.res.writableEnded) {
            this.res.end();
        }
    }
}
 
// Middleware to parse JSON bodies
app.use(express.json());
app.use(cors());
 
app.post('/', async (req, res) => {
 
    const sse = new SSEWrapper(res);
 
    const event = {
        headers: req.headers,
        body: JSON.stringify(req.body)
    }
 
    const params = await extractParams(event);
 
    const returnResponse = (responseStream, response) => {
        sse.returnResponse(response);
    }
 
    await routeRequest(params, returnResponse, sse);
});
 
// Start the server
app.listen(port, () => {
    console.log(`Server is running on http://localhost:${port}`);
});