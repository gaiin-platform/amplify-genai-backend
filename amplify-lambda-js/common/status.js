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