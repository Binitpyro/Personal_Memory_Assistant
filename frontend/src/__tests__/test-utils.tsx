import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Toaster } from 'sonner';

export function renderWithProviders(ui: React.ReactElement, { route = '/' } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });

  return {
    ...render(
      // The Toaster is mounted here because destructive confirmations are
      // sonner action-toasts rather than `confirm()`. Without it the action
      // button never enters the DOM and those flows are untestable - which is
      // why the folder-removal tests used to stub `window.confirm` instead.
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          {ui}
          <Toaster />
        </MemoryRouter>
      </QueryClientProvider>
    ),
    queryClient,
  };
}
