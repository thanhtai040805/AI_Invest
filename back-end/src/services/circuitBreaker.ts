/**
 * Circuit Breaker for external service calls.
 *
 * States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)
 * Prevents cascading failures when ai-engine is down.
 */

type CircuitState = 'CLOSED' | 'OPEN' | 'HALF_OPEN';

interface CircuitBreakerOptions {
  failureThreshold: number;
  recoveryTimeoutMs: number;
  halfOpenMaxAttempts: number;
}

const DEFAULT_OPTIONS: CircuitBreakerOptions = {
  failureThreshold: 5,
  recoveryTimeoutMs: 30_000,
  halfOpenMaxAttempts: 3,
};

export class CircuitBreaker {
  private state: CircuitState = 'CLOSED';
  private failures = 0;
  private successes = 0;
  private lastFailureTime = 0;
  private readonly options: CircuitBreakerOptions;

  constructor(options: Partial<CircuitBreakerOptions> = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  async execute<T>(fn: () => Promise<T>): Promise<T> {
    this.checkState();

    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (error) {
      this.onFailure();
      throw error;
    }
  }

  private checkState(): void {
    if (this.state === 'CLOSED') return;

    if (this.state === 'OPEN') {
      const now = Date.now();
      if (now - this.lastFailureTime >= this.options.recoveryTimeoutMs) {
        this.state = 'HALF_OPEN';
        this.successes = 0;
        console.log('[CircuitBreaker] State: OPEN → HALF_OPEN');
      } else {
        throw new Error('Circuit breaker is OPEN — service unavailable');
      }
    }

    if (this.state === 'HALF_OPEN' && this.successes >= this.options.halfOpenMaxAttempts) {
      this.state = 'CLOSED';
      this.failures = 0;
      console.log('[CircuitBreaker] State: HALF_OPEN → CLOSED (recovered)');
    }
  }

  private onSuccess(): void {
    this.failures = 0;
    if (this.state === 'HALF_OPEN') {
      this.successes++;
    }
  }

  private onFailure(): void {
    this.failures++;
    this.lastFailureTime = Date.now();

    if (this.state === 'HALF_OPEN') {
      this.state = 'OPEN';
      console.log('[CircuitBreaker] State: HALF_OPEN → OPEN (probe failed)');
      return;
    }

    if (this.failures >= this.options.failureThreshold) {
      this.state = 'OPEN';
      console.log(`[CircuitBreaker] State: CLOSED → OPEN (${this.failures} failures)`);
    }
  }

  get currentState(): CircuitState {
    this.checkState();
    return this.state;
  }

  get isAvailable(): boolean {
    return this.currentState !== 'OPEN';
  }

  reset(): void {
    this.state = 'CLOSED';
    this.failures = 0;
    this.successes = 0;
    this.lastFailureTime = 0;
  }

  get stats(): { state: CircuitState; failures: number; successes: number } {
    return {
      state: this.currentState,
      failures: this.failures,
      successes: this.successes,
    };
  }
}
