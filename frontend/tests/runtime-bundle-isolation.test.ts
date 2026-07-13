import { describe, expect, it } from 'vitest';
// @ts-expect-error Vitest runs tests in Node; the frontend compiler intentionally omits Node types.
import { readFileSync } from 'node:fs';

describe('runtime bundle isolation', () => {
  it('loads the Agent and Manager entry graphs only after runtime selection', () => {
    const router = readFileSync('src/app/router.tsx', 'utf8');
    expect(router).toContain("lazy(() => import('./AgentEntry'))");
    expect(router).toContain("lazy(() => import('./ManagerEntry'))");
    expect(router).not.toContain('../features/');
  });
});
