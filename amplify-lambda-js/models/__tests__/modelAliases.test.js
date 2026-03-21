/**
 * Unit Tests for Model Alias Resolution
 *
 * To run these tests, first install Jest:
 *   npm install --save-dev jest
 *
 * Then add to package.json:
 *   "scripts": {
 *     "test": "node --experimental-vm-modules node_modules/jest/bin/jest.js"
 *   }
 *
 * Run tests:
 *   npm test
 */

import { jest } from '@jest/globals';
import { resolveModelAlias, isAlias, getAllAliases, getReverseMapping } from '../modelAliases.js';

describe('Model Alias Resolution', () => {

    describe('resolveModelAlias', () => {

        test('should resolve a known alias to the correct model ID', () => {
            const result = resolveModelAlias('opus-latest');

            expect(result.resolvedId).toBe('us.anthropic.claude-opus-4-6-v1:0');
            expect(result.wasAlias).toBe(true);
            expect(result.aliasInfo).not.toBeNull();
            expect(result.aliasInfo.alias).toBe('opus-latest');
            expect(result.aliasInfo.category).toBe('claude');
            expect(result.aliasInfo.tier).toBe('premium');
        });

        test('should resolve sonnet-latest correctly', () => {
            const result = resolveModelAlias('sonnet-latest');

            expect(result.resolvedId).toBe('us.anthropic.claude-sonnet-4-6-v1:0');
            expect(result.wasAlias).toBe(true);
            expect(result.aliasInfo.tier).toBe('balanced');
        });

        test('should resolve haiku-latest correctly', () => {
            const result = resolveModelAlias('haiku-latest');

            expect(result.resolvedId).toBe('us.anthropic.claude-haiku-4-5-20251001-v1:0');
            expect(result.wasAlias).toBe(true);
            expect(result.aliasInfo.tier).toBe('fast');
        });

        test('should pass through a non-alias model ID unchanged', () => {
            const directModelId = 'us.anthropic.claude-3-5-sonnet-20241022-v2:0';
            const result = resolveModelAlias(directModelId);

            expect(result.resolvedId).toBe(directModelId);
            expect(result.wasAlias).toBe(false);
            expect(result.aliasInfo).toBeNull();
        });

        test('should handle null input gracefully', () => {
            const result = resolveModelAlias(null);

            expect(result.resolvedId).toBeNull();
            expect(result.wasAlias).toBe(false);
            expect(result.aliasInfo).toBeNull();
        });

        test('should handle undefined input gracefully', () => {
            const result = resolveModelAlias(undefined);

            expect(result.resolvedId).toBeUndefined();
            expect(result.wasAlias).toBe(false);
            expect(result.aliasInfo).toBeNull();
        });

        test('should handle empty string gracefully', () => {
            const result = resolveModelAlias('');

            expect(result.resolvedId).toBe('');
            expect(result.wasAlias).toBe(false);
            expect(result.aliasInfo).toBeNull();
        });

        test('should handle non-existent alias as pass-through', () => {
            const fakeAlias = 'this-does-not-exist';
            const result = resolveModelAlias(fakeAlias);

            expect(result.resolvedId).toBe(fakeAlias);
            expect(result.wasAlias).toBe(false);
            expect(result.aliasInfo).toBeNull();
        });
    });

    describe('isAlias', () => {

        test('should return true for known alias', () => {
            expect(isAlias('opus-latest')).toBe(true);
            expect(isAlias('sonnet-latest')).toBe(true);
            expect(isAlias('haiku-latest')).toBe(true);
        });

        test('should return false for non-alias model ID', () => {
            expect(isAlias('us.anthropic.claude-opus-4-6-v1:0')).toBe(false);
        });

        test('should return false for null', () => {
            expect(isAlias(null)).toBe(false);
        });

        test('should return false for undefined', () => {
            expect(isAlias(undefined)).toBe(false);
        });

        test('should return false for empty string', () => {
            expect(isAlias('')).toBe(false);
        });

        test('should return false for non-string input', () => {
            expect(isAlias(123)).toBe(false);
            expect(isAlias({})).toBe(false);
            expect(isAlias([])).toBe(false);
        });
    });

    describe('getAllAliases', () => {

        test('should return all aliases without error', () => {
            const result = getAllAliases();

            expect(result.error).toBe(false);
            expect(result.aliases).toBeDefined();
            expect(typeof result.aliases).toBe('object');
        });

        test('should include all expected aliases', () => {
            const result = getAllAliases();
            const aliases = result.aliases;

            expect(aliases['opus-latest']).toBeDefined();
            expect(aliases['sonnet-latest']).toBeDefined();
            expect(aliases['haiku-latest']).toBeDefined();
            expect(aliases['claude-opus-latest']).toBeDefined();
            expect(aliases['claude-sonnet-latest']).toBeDefined();
            expect(aliases['claude-haiku-latest']).toBeDefined();
        });

        test('should return complete alias configuration', () => {
            const result = getAllAliases();
            const opusAlias = result.aliases['opus-latest'];

            expect(opusAlias.resolves_to).toBeDefined();
            expect(opusAlias.description).toBeDefined();
            expect(opusAlias.category).toBeDefined();
            expect(opusAlias.tier).toBeDefined();
        });
    });

    describe('getReverseMapping', () => {

        test('should create reverse mapping from model ID to aliases', () => {
            const reverseMap = getReverseMapping();

            expect(reverseMap).toBeDefined();
            expect(typeof reverseMap).toBe('object');
        });

        test('should map Opus model to both opus-latest and claude-opus-latest', () => {
            const reverseMap = getReverseMapping();
            const opusModelId = 'us.anthropic.claude-opus-4-6-v1:0';
            const opusAliases = reverseMap[opusModelId];

            expect(opusAliases).toBeDefined();
            expect(Array.isArray(opusAliases)).toBe(true);
            expect(opusAliases.length).toBeGreaterThanOrEqual(2);

            const aliasNames = opusAliases.map(a => a.alias);
            expect(aliasNames).toContain('opus-latest');
            expect(aliasNames).toContain('claude-opus-latest');
        });

        test('should include alias metadata in reverse mapping', () => {
            const reverseMap = getReverseMapping();
            const opusModelId = 'us.anthropic.claude-opus-4-6-v1:0';
            const opusAliases = reverseMap[opusModelId];

            const firstAlias = opusAliases[0];
            expect(firstAlias.alias).toBeDefined();
            expect(firstAlias.description).toBeDefined();
            expect(firstAlias.category).toBeDefined();
            expect(firstAlias.tier).toBeDefined();
        });
    });

    describe('Integration scenarios', () => {

        test('should handle complete request flow with alias', () => {
            // Simulate user request with alias
            const userRequestedModel = 'sonnet-latest';

            // Step 1: Check if it's an alias
            const isAliasCheck = isAlias(userRequestedModel);
            expect(isAliasCheck).toBe(true);

            // Step 2: Resolve the alias
            const resolution = resolveModelAlias(userRequestedModel);
            expect(resolution.wasAlias).toBe(true);
            expect(resolution.resolvedId).toBe('us.anthropic.claude-sonnet-4-6-v1:0');

            // Step 3: Use resolved ID for API call
            const actualModelId = resolution.resolvedId;
            expect(actualModelId).toMatch(/^us\.anthropic\.claude-/);
        });

        test('should handle complete request flow without alias', () => {
            // Simulate user request with direct model ID
            const userRequestedModel = 'us.anthropic.claude-opus-4-6-v1:0';

            // Step 1: Check if it's an alias
            const isAliasCheck = isAlias(userRequestedModel);
            expect(isAliasCheck).toBe(false);

            // Step 2: Resolve (should pass through)
            const resolution = resolveModelAlias(userRequestedModel);
            expect(resolution.wasAlias).toBe(false);
            expect(resolution.resolvedId).toBe(userRequestedModel);

            // Step 3: Use original ID for API call
            const actualModelId = resolution.resolvedId;
            expect(actualModelId).toBe(userRequestedModel);
        });
    });

    describe('Performance', () => {

        test('should resolve aliases quickly (< 1ms)', () => {
            const startTime = Date.now();

            // Resolve 100 aliases
            for (let i = 0; i < 100; i++) {
                resolveModelAlias('opus-latest');
            }

            const endTime = Date.now();
            const totalTime = endTime - startTime;
            const avgTime = totalTime / 100;

            // Should be well under 1ms per resolution
            expect(avgTime).toBeLessThan(1);
        });

        test('should handle getAllAliases efficiently', () => {
            const startTime = Date.now();

            // Get all aliases 100 times
            for (let i = 0; i < 100; i++) {
                getAllAliases();
            }

            const endTime = Date.now();
            const totalTime = endTime - startTime;

            // Should complete quickly (cached)
            expect(totalTime).toBeLessThan(100); // 100 calls in < 100ms
        });
    });
});
