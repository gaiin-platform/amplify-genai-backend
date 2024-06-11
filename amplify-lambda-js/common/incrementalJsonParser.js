//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

export const parseValue = (str) => {
    str = str.trim();
    if(str.startsWith("null")){
        return {value: null, tail: str.substring(4)};
    }
    else if(str.startsWith("[")){
        return parseArray(str);
    }
    else if(str.startsWith("{")){
        return parseObject(str);
    }
    else if(str.startsWith("\"")){
        return parseString(str);
    }
    else if(str.startsWith("f") || str.startsWith("t")){
        return parseBoolean(str);
    }
    else {
        return parseNumber(str);
    }
}

export const parseObject = (str) => {
    str = str.trim();

    if(!str.startsWith("{")){
        throw new Exception("Objects must start with {.");
    }

    str = str.substring(1).trim();

    let obj = {};
    let newTail = str;

    while(newTail && !newTail.startsWith("}")){
        let {value:key, tail:keyTail} = parseValue(newTail);
        if(!keyTail){
            return {value: obj, tail: null};
        }
        if(keyTail && keyTail.trim().length > 0){
            keyTail = keyTail.trim();
            if(keyTail.startsWith("}")){
                return {value: obj, tail: keyTail.substring(1)};
            }
            else if(keyTail.startsWith(":")){
                keyTail = keyTail.substring(1);
            }
            else if(keyTail.trim().length === 0) {
                return {value: obj, tail: keyTail};
            }

            keyTail = keyTail.trim();

            let {value, tail} = parseValue(keyTail);

            obj[key] = value;

            if(tail && tail.trim().startsWith(",")){
                tail = tail.trim().substring(1);
            }
            if(!tail || tail.trim().length === 0){
                return {value: obj, tail: tail};
            }

            newTail = tail.trim();
        }
    }

    if(newTail && newTail.trim().startsWith("}")){
        newTail = newTail.trim().substring(1);
    }

    return {value: obj, tail: newTail};
}

export const parseArray = (str) => {
    str = str.trim();

    if(!str.startsWith("[")){
        throw new Exception("Arrays must start with [.");
    }

    let newTail = str.slice(1).trim();
    const arr = [];
    while(!newTail.startsWith("]")){
        if(newTail.startsWith(",")){
            newTail = newTail.substring(1);
        }
        if(!newTail.startsWith("]")) {
            const {value, tail} = parseValue(newTail);
            arr.push(value);

            if (!tail || tail.trim().length === 0) {
                return {value: arr, tail: null};
            }

            newTail = tail.trim();
        } else{
            return {value: arr, tail: newTail.substring(1).trim()};
        }
    }

    return {value: arr, tail: newTail.substring(1)};
}

export const parseNumber = (str) => {
    const {value, tail} = parseToken(str,c => "-0123456789.".indexOf(c) < 0);
    let ival = 0;
    try {
        ival = Number.parseFloat(str);
    }catch(e){}
    return {value: ival, tail}
}

export const parseBoolean = (str) => {
    const {value, tail} = parseToken(str,(c) => " }],:".indexOf(c) > -1);
    return {value: value.trim() === 'true', tail}
}

export const parseToken = (str, breaks) => {
    let value = '';
    let index = 0;
    for(let i = 0; i < str.length; i++){
        index = i;
        const c = str.charAt(i);
        if(breaks(c)){
            return {value: value, tail: str.substr(index)};
        }
        value += c;
    }
    return {value: value, tail: null};
}


export const parseString = (str) => {
    let value = "";

    str = str.trim();

    if(!str.startsWith("\"")){
        throw new Exception("Strings must start with quotes.");
    }

    str = str.slice(1);

    let lastChar = null;
    let index = 0;

    for(let i = 0; i < str.length; i++){
        index = i;
        const c = str.charAt(i);
        if(c === '"' && lastChar !== '\\'){
            break;
        }
        else {
            value += c;
            lastChar = c;
        }
    }

    return {value: value, tail: str.slice(index + 1)};
}