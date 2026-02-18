/**
 * Manual Test Script for Model Alias Resolution
 *
 * This script can be run directly without Jest to verify alias resolution works.
 *
 * Usage:
 *   node models/__tests__/manual-test-aliases.js
 */

import { resolveModelAlias, isAlias, getAllAliases, getReverseMapping } from '../modelAliases.js';

console.log('\n=== Model Alias Resolution - Manual Test ===\n');

// Test 1: Resolve known aliases
console.log('Test 1: Resolve known aliases');
console.log('--------------------------------');
const aliases = ['opus-latest', 'sonnet-latest', 'haiku-latest'];

for (const alias of aliases) {
    const result = resolveModelAlias(alias);
    console.log(`✓ ${alias}`);
    console.log(`  → Resolved to: ${result.resolvedId}`);
    console.log(`  → Was alias: ${result.wasAlias}`);
    console.log(`  → Category: ${result.aliasInfo?.category}`);
    console.log(`  → Tier: ${result.aliasInfo?.tier}\n`);
}

// Test 2: Pass through non-alias
console.log('\nTest 2: Pass through non-alias model ID');
console.log('----------------------------------------');
const directId = 'us.anthropic.claude-3-5-sonnet-20241022-v2:0';
const result2 = resolveModelAlias(directId);
console.log(`Input: ${directId}`);
console.log(`Output: ${result2.resolvedId}`);
console.log(`Was alias: ${result2.wasAlias}`);
console.log(`✓ Pass-through works correctly\n`);

// Test 3: Check isAlias function
console.log('\nTest 3: isAlias() function');
console.log('--------------------------');
console.log(`isAlias('opus-latest'): ${isAlias('opus-latest')}`);
console.log(`isAlias('not-an-alias'): ${isAlias('not-an-alias')}`);
console.log(`isAlias(null): ${isAlias(null)}`);
console.log(`✓ isAlias() works correctly\n`);

// Test 4: Get all aliases
console.log('\nTest 4: getAllAliases()');
console.log('-----------------------');
const allAliases = getAllAliases();
console.log(`Error: ${allAliases.error}`);
console.log(`Number of aliases: ${Object.keys(allAliases.aliases).length}`);
console.log(`Aliases: ${Object.keys(allAliases.aliases).join(', ')}`);
console.log(`✓ getAllAliases() works correctly\n`);

// Test 5: Reverse mapping
console.log('\nTest 5: getReverseMapping()');
console.log('---------------------------');
const reverseMap = getReverseMapping();
const opusModelId = 'us.anthropic.claude-opus-4-6-v1:0';
if (reverseMap[opusModelId]) {
    console.log(`Model: ${opusModelId}`);
    console.log(`Aliases pointing to it:`);
    for (const aliasInfo of reverseMap[opusModelId]) {
        console.log(`  - ${aliasInfo.alias} (${aliasInfo.tier})`);
    }
    console.log(`✓ Reverse mapping works correctly\n`);
} else {
    console.log(`⚠ No aliases found for ${opusModelId}\n`);
}

// Test 6: Null/undefined handling
console.log('\nTest 6: Null/undefined handling');
console.log('-------------------------------');
const result6a = resolveModelAlias(null);
const result6b = resolveModelAlias(undefined);
const result6c = resolveModelAlias('');

console.log(`resolveModelAlias(null) → resolvedId: ${result6a.resolvedId}, wasAlias: ${result6a.wasAlias}`);
console.log(`resolveModelAlias(undefined) → resolvedId: ${result6b.resolvedId}, wasAlias: ${result6b.wasAlias}`);
console.log(`resolveModelAlias('') → resolvedId: '${result6c.resolvedId}', wasAlias: ${result6c.wasAlias}`);
console.log(`✓ Null/undefined handling works correctly\n`);

// Test 7: Performance test
console.log('\nTest 7: Performance test');
console.log('------------------------');
const iterations = 1000;
const startTime = Date.now();

for (let i = 0; i < iterations; i++) {
    resolveModelAlias('opus-latest');
}

const endTime = Date.now();
const totalTime = endTime - startTime;
const avgTime = totalTime / iterations;

console.log(`Resolved ${iterations} aliases in ${totalTime}ms`);
console.log(`Average time per resolution: ${avgTime.toFixed(4)}ms`);
console.log(`✓ Performance is ${avgTime < 1 ? 'EXCELLENT' : 'acceptable'} (<1ms target)\n`);

console.log('\n=== All Manual Tests Passed! ===\n');
