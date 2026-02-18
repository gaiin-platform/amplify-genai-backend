//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

/**
 * LZW Compression/Decompression utilities
 * Used for artifact content compression
 */

export function lzwCompress(strInput) {
    if (!strInput) return [];
    const dictionary = new Map();
    let nextCode = 256;
    let compressedOutput = [];

    for (let i = 0; i < 256; i++) {
        dictionary.set(String.fromCharCode(i), i);
    }

    // Preprocessing to convert Unicode characters to a unique format
    const processedInput = strInput.split('').map(char => {
        return char.charCodeAt(0) > 255 ? `U+${char.charCodeAt(0).toString(16)}` : char;
    }).join('');

    let currentPattern = '';
    for (let character of processedInput) {
        const newPattern = currentPattern + character;
        if (dictionary.has(newPattern)) {
            currentPattern = newPattern;
        } else {
            compressedOutput.push(dictionary.get(currentPattern));
            dictionary.set(newPattern, nextCode++);
            currentPattern = character;
        }
    }

    if (currentPattern !== '') {
        compressedOutput.push(dictionary.get(currentPattern));
    }

    return compressedOutput;
}

export function lzwUncompress(compressedData) {
    if (!compressedData || compressedData.length === 0) return '';

    const dictionary = new Map();
    for (let i = 0; i < 256; i++) {
        dictionary.set(i, String.fromCharCode(i));
    }

    let decompressedString = '';
    let previousEntry = dictionary.get(compressedData[0]);
    if (!previousEntry) {
        return '';
    }
    decompressedString += previousEntry;

    let nextCode = 256;
    for (let i = 1; i < compressedData.length; i++) {
        const currentCode = compressedData[i];
        let currentEntry;
        if (dictionary.has(currentCode)) {
            currentEntry = dictionary.get(currentCode);
        } else if (currentCode === nextCode) {
            currentEntry = previousEntry + previousEntry.charAt(0);
        } else {
            throw new Error('Invalid compressed data: Entry for code not found');
        }

        decompressedString += currentEntry;
        dictionary.set(nextCode++, previousEntry + currentEntry.charAt(0));
        previousEntry = currentEntry;
    }

    // Postprocessing to convert the tagged Unicode characters back to their original form
    const unicodePattern = /U\+([0-9a-f]{4})/gi;
    const output = decompressedString.replace(unicodePattern, (_, hex) => {
        return String.fromCharCode(parseInt(hex, 16));
    });

    return output;
}

export function isLzwCompressed(data) {
    return Array.isArray(data) && data.length > 0 && data.every(item => typeof item === 'number');
}
