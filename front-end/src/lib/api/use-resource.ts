"use client"
import { useCallback, useEffect, useRef, useState } from "react"
import type { ApiProblem } from "./client"
export function useResource<T>(loader: () => Promise<T>, deps: readonly unknown[] = []) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState<ApiProblem | null>(null); const [loading, setLoading] = useState(true)
  const requestId = useRef(0)
  const loaderRef = useRef(loader)
  const dependencyKey = JSON.stringify(deps)
  const reload = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true); setError(null)
    try { const result = await loaderRef.current(); if (id === requestId.current) setData(result) }
    catch (value) { if (id === requestId.current) setError(value as ApiProblem) }
    finally { if (id === requestId.current) setLoading(false) }
  }, [])
  useEffect(() => { loaderRef.current = loader }, [loader])
  // Resource loading is the external synchronization performed by this hook.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { const requests = requestId; void reload(); return () => { requests.current++ } }, [reload, dependencyKey])
  return { data, error, loading, reload }
}
