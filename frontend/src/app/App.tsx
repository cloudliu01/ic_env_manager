import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { useState } from 'react';
import { RuntimeProvider } from './RuntimeProvider';
import { RuntimeRouter } from './router';

export function App() {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  }));
  return <QueryClientProvider client={queryClient}><BrowserRouter><RuntimeProvider><RuntimeRouter /></RuntimeProvider></BrowserRouter></QueryClientProvider>;
}
