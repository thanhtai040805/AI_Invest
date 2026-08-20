import { DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_NAME } from "./branding";
import { clientErrorMessage } from "../i18n/client-errors";

export type PetAgentFacing = "left" | "right";

export type PetAgentActivity =
  | "idle"
  | "thinking"
  | "searching"
  | "working"
  | "answering"
  | "done"
  | "error";

export type PetAgentMotion = "idle" | "wave" | "jump" | "fly" | "roam" | "dance";
export type PetAgentSpeechTone = "neutral" | "active" | "success" | "error";

export interface PetAgentIdentity {
  id: string;
  name: string;
  avatar: string;
  serialNumber?: string | number;
  size: number;
}

export interface PetAgentPoint {
  x: number;
  y: number;
}

export interface PetAgentSpeech {
  text: string;
  detail?: string;
  threadId?: string;
  tone: PetAgentSpeechTone;
}

export interface PetAgentSnapshot {
  identity: PetAgentIdentity;
  facing: PetAgentFacing;
  position: PetAgentPoint | null;
  look: PetAgentPoint;
  activity: PetAgentActivity;
  motion: PetAgentMotion;
  expression: string | null;
  speech: PetAgentSpeech | null;
  flightLift: number;
  revision: number;
}

export interface PetAgentOptions {
  id?: string;
  name: string;
  avatar?: string;
  serialNumber?: string | number;
  size?: number;
  facing?: PetAgentFacing;
  position?: PetAgentPoint | null;
}

export interface PetAgentSayOptions {
  detail?: string;
  threadId?: string;
  tone?: PetAgentSpeechTone;
  duration?: number | null;
}

export interface PetAgentActivityOptions extends Omit<PetAgentSayOptions, "tone"> {
  duration?: number | null;
}

export interface PetAgentMotionOptions {
  duration?: number;
  height?: number;
  queue?: boolean;
}

export interface PetAgentMotionCommand extends Omit<PetAgentMotionOptions, "queue"> {
  motion: Exclude<PetAgentMotion, "idle">;
}

type Listener = () => void;
type Timer = ReturnType<typeof setTimeout>;

const MOTION_DURATION: Record<Exclude<PetAgentMotion, "idle">, number> = {
  wave: 1_450,
  jump: 760,
  fly: 6_800,
  roam: 14_000,
  dance: 5_200,
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function activityTone(activity: PetAgentActivity): PetAgentSpeechTone {
  if (activity === "done") return "success";
  if (activity === "error") return "error";
  if (activity === "idle") return "neutral";
  return "active";
}

/**
 * Stateful game-character API. Commands are chainable:
 * `new PetAgent({ name: "Tiểu Trí", avatar: "@_@", size: 1 }).left().say("Tìm thấy rồi").jump()`.
 */
export class PetAgent {
  private listeners = new Set<Listener>();
  private speechTimer: Timer | null = null;
  private activityTimer: Timer | null = null;
  private expressionTimer: Timer | null = null;
  private motionTimer: Timer | null = null;
  private motionQueue: PetAgentMotionCommand[] = [];

  private state: PetAgentSnapshot;

  constructor(options: PetAgentOptions) {
    this.state = {
      identity: {
        id: options.id ?? `pet-${String(options.serialNumber ?? "primary")}`,
        name: options.name || DEFAULT_AGENT_NAME,
        avatar: options.avatar ?? DEFAULT_AGENT_AVATAR,
        serialNumber: options.serialNumber,
        size: clamp(options.size ?? 1, 0.72, 1.35),
      },
      facing: options.facing ?? "right",
      position: options.position ?? null,
      look: { x: 0, y: 0 },
      activity: "idle",
      motion: "idle",
      expression: null,
      speech: null,
      flightLift: 240,
      revision: 0,
    };
  }

  getSnapshot = () => this.state;

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  configure(options: Partial<Omit<PetAgentIdentity, "id">> & { id?: string }) {
    const identity = {
      ...this.state.identity,
      ...options,
      name: options.name || this.state.identity.name,
      avatar: options.avatar ?? this.state.identity.avatar,
      size: clamp(options.size ?? this.state.identity.size, 0.72, 1.35),
    };
    this.patch({ identity });
    return this;
  }

  rename(name: string) {
    return this.configure({ name });
  }

  setAvatar(avatar: string) {
    return this.configure({ avatar });
  }

  face(direction: PetAgentFacing) {
    if (this.state.facing !== direction) this.patch({ facing: direction });
    return this;
  }

  left() {
    return this.face("left");
  }

  right() {
    return this.face("right");
  }

  moveTo(position: PetAgentPoint): this;
  moveTo(x: number, y: number): this;
  moveTo(positionOrX: PetAgentPoint | number, y?: number) {
    const position =
      typeof positionOrX === "number"
        ? { x: positionOrX, y: y ?? this.state.position?.y ?? 0 }
        : positionOrX;
    this.patch({ position });
    return this;
  }

  lookAt(point: PetAgentPoint): this;
  lookAt(x: number, y: number): this;
  lookAt(pointOrX: PetAgentPoint | number, y?: number) {
    const point =
      typeof pointOrX === "number" ? { x: pointOrX, y: y ?? 0 } : pointOrX;
    const look = { x: clamp(point.x, -1, 1), y: clamp(point.y, -1, 1) };
    if (look.x !== this.state.look.x || look.y !== this.state.look.y) this.patch({ look });
    return this;
  }

  resetLook() {
    return this.lookAt(0, 0);
  }

  emote(expression: string, duration: number | null = 1_800) {
    this.clearExpressionTimer();
    const next = expression.trim() || null;
    if (this.state.expression !== next) this.patch({ expression: next });
    if (next && duration !== null && duration > 0) {
      this.expressionTimer = setTimeout(() => {
        this.expressionTimer = null;
        if (this.state.expression === next) this.patch({ expression: null });
      }, duration);
    }
    return this;
  }

  clearExpression() {
    this.clearExpressionTimer();
    if (this.state.expression) this.patch({ expression: null });
    return this;
  }

  say(text: string, options: PetAgentSayOptions = {}) {
    this.clearTimer("speech");
    const speech = text.trim()
      ? {
          text: text.trim(),
          detail: options.detail,
          threadId: options.threadId,
          tone: options.tone ?? "neutral",
        }
      : null;
    this.patch({ speech });

    const duration = options.duration === undefined ? 4_200 : options.duration;
    if (speech && duration !== null && duration > 0) {
      this.speechTimer = setTimeout(() => {
        this.speechTimer = null;
        this.patch({ speech: null });
      }, duration);
    }
    return this;
  }

  quiet() {
    this.clearTimer("speech");
    if (this.state.speech) this.patch({ speech: null });
    return this;
  }

  idle(clearSpeech = true) {
    this.clearTimer("activity");
    this.clearExpression();
    this.interruptMotion();
    if (clearSpeech) this.quiet();
    if (this.state.activity !== "idle") this.patch({ activity: "idle" });
    return this;
  }

  think(text = clientErrorMessage("petThinking"), options: PetAgentActivityOptions = {}) {
    return this.setActivity("thinking", text, options);
  }

  search(text = clientErrorMessage("petSearching"), options: PetAgentActivityOptions = {}) {
    return this.setActivity("searching", text, options);
  }

  work(text = clientErrorMessage("petWorking"), options: PetAgentActivityOptions = {}) {
    return this.setActivity("working", text, options);
  }

  answer(text = clientErrorMessage("petAnswering"), options: PetAgentActivityOptions = {}) {
    return this.setActivity("answering", text, options);
  }

  complete(text = clientErrorMessage("petComplete"), options: PetAgentActivityOptions = {}) {
    const duration = options.duration === undefined ? 6_200 : options.duration;
    this.setActivity("done", text, { ...options, duration });
    this.sequence({ motion: "jump" }, { motion: "wave" });
    return this;
  }

  fail(text = clientErrorMessage("petFailed"), options: PetAgentActivityOptions = {}) {
    const duration = options.duration === undefined ? 6_200 : options.duration;
    return this.setActivity("error", text, { ...options, duration });
  }

  act(motion: Exclude<PetAgentMotion, "idle">, options: PetAgentMotionOptions = {}) {
    const command: PetAgentMotionCommand = {
      motion,
      duration: options.duration,
      height: options.height,
    };
    if (options.queue) {
      this.motionQueue.push(command);
      if (this.state.motion === "idle") this.startNextMotion();
      return this;
    }
    this.clearMotion();
    this.motionQueue.push(command);
    this.startNextMotion();
    return this;
  }

  wave(options: Omit<PetAgentMotionOptions, "height"> = {}) {
    return this.act("wave", options);
  }

  jump(options: Omit<PetAgentMotionOptions, "height"> = {}) {
    return this.act("jump", options);
  }

  fly(options: PetAgentMotionOptions = {}) {
    return this.act("fly", options);
  }

  roam(options: Omit<PetAgentMotionOptions, "height"> = {}) {
    return this.act("roam", options);
  }

  dance(options: Omit<PetAgentMotionOptions, "height"> = {}) {
    return this.act("dance", options);
  }

  sequence(...commands: PetAgentMotionCommand[]) {
    this.clearMotion();
    this.motionQueue.push(...commands);
    this.startNextMotion();
    return this;
  }

  stop() {
    this.idle();
    this.resetLook();
    return this;
  }

  destroy() {
    this.clearTimer("speech");
    this.clearTimer("activity");
    this.clearExpressionTimer();
    this.clearMotion();
    this.listeners.clear();
  }

  private setActivity(
    activity: Exclude<PetAgentActivity, "idle">,
    text: string,
    options: PetAgentActivityOptions,
  ) {
    this.clearTimer("activity");
    this.clearExpression();
    this.interruptMotion();
    if (this.state.activity !== activity) this.patch({ activity });
    this.say(text, {
      ...options,
      tone: activityTone(activity),
      duration: options.duration ?? null,
    });

    if (options.duration !== undefined && options.duration !== null && options.duration > 0) {
      this.activityTimer = setTimeout(() => {
        this.activityTimer = null;
        if (this.state.activity === activity) this.idle();
      }, options.duration);
    }
    return this;
  }

  private interruptMotion() {
    this.clearMotion();
    if (this.state.motion !== "idle") this.patch({ motion: "idle" });
  }

  private clearMotion() {
    if (this.motionTimer) clearTimeout(this.motionTimer);
    this.motionTimer = null;
    this.motionQueue = [];
  }

  private startNextMotion() {
    const command = this.motionQueue.shift();
    if (!command) {
      if (this.state.motion !== "idle") this.patch({ motion: "idle" });
      return;
    }

    this.patch({
      motion: command.motion,
      flightLift:
        command.motion === "fly"
          ? clamp(command.height ?? this.state.flightLift, 24, 360)
          : this.state.flightLift,
    });
    this.motionTimer = setTimeout(() => {
      this.motionTimer = null;
      this.startNextMotion();
    }, command.duration ?? MOTION_DURATION[command.motion]);
  }

  private clearTimer(kind: "speech" | "activity") {
    const timer = kind === "speech" ? this.speechTimer : this.activityTimer;
    if (timer) clearTimeout(timer);
    if (kind === "speech") this.speechTimer = null;
    else this.activityTimer = null;
  }

  private clearExpressionTimer() {
    if (this.expressionTimer) clearTimeout(this.expressionTimer);
    this.expressionTimer = null;
  }

  private patch(patch: Partial<PetAgentSnapshot>) {
    this.state = { ...this.state, ...patch, revision: this.state.revision + 1 };
    this.listeners.forEach((listener) => listener());
  }
}
