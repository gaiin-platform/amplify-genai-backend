//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { v4 as uuidv4 } from 'uuid';


export const newStatus = (data) => {
    return {
        id:uuidv4(),
        summary: '',
        message: '',
        type: "info",
        inProgress:false,
        ...data,
    }
}


export function getThinkingMessage() {
    const messages = [
        "Thinking...",
        "Contemplating...",
        "Processing...",
        "Analyzing...",
        "Computing...",
        "Working on it...",
        "Calculating...",
        "Evaluating..."
    ];
    return messages[Math.floor(Math.random() * messages.length)];
}