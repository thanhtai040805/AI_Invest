'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { screenerAPI, ScreenerFilters } from '@/services/api';

export function useScreenerFilter(filters: ScreenerFilters, enabled = true) {
  return useQuery({
    queryKey: ['screener', filters],
    queryFn: () => screenerAPI.filter(filters),
    enabled,
    staleTime: 30_000,
  });
}

export function useBuiltinPresets() {
  return useQuery({
    queryKey: ['screener', 'presets', 'builtin'],
    queryFn: () => screenerAPI.getBuiltinPresets(),
    staleTime: 60 * 60 * 1000,
  });
}

export function useScreenerPresets(enabled = true) {
  return useQuery({
    queryKey: ['screener', 'presets'],
    queryFn: () => screenerAPI.getPresets(),
    enabled,
    retry: false,
  });
}

export function useSaveScreenerPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, filters }: { name: string; filters: ScreenerFilters }) =>
      screenerAPI.savePreset(name, filters),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['screener', 'presets'] }),
  });
}

export function exportScreenerCsv(rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const headers = ['symbol', 'name', 'price', 'changePercent', 'volume', 'pe', 'pb', 'roe', 'de', 'rsi', 'signal'];
  const lines = [
    headers.join(','),
    ...rows.map((r) =>
      headers.map((h) => {
        const v = r[h];
        const s = v == null ? '' : String(v);
        return s.includes(',') ? `"${s}"` : s;
      }).join(','),
    ),
  ];
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `screener_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
