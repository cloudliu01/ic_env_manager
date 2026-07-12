import { describe, expect, it } from 'vitest';

declare global {
  interface ImportMeta {
    glob: (pattern: string, options: { eager: boolean }) => Record<string, unknown>;
  }
}

const legacyManagerModules = import.meta.glob('../src/{agents/{AgentContext,AgentStateContext,AgentSelector},pages/{AppRoutes,HostOverviewPage},api/{agents,fleet}}.tsx', { eager: true });

describe('manager route ownership', () => {
  it('removes legacy active-Agent state and duplicate Manager models', () => {
    expect(legacyManagerModules).toEqual({});
  });
});
