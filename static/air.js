// Turns phone orientation into cursor movement, independent of how the phone is held.
//
// Input: a quaternion [x, y, z, w] rotating screen coordinates into a world frame
// whose z axis points up (what RelativeOrientationSensor gives with
// referenceFrame: 'screen'). Output: how far the pointer turned, in degrees.
//
//   horizontal = rotation around the world's vertical axis (turning left/right)
//   vertical   = rotation around the screen's left-right axis, kept level
//                (tilting the pointing direction up/down)
//
// Neither depends on the grip, so flat, upright and landscape all behave the same,
// and twisting the wrist (roll) is ignored.
'use strict';

const RAD2DEG = 180 / Math.PI;

function qmul(a, b) {
  const [ax, ay, az, aw] = a, [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

function qconj(q) { return [-q[0], -q[1], -q[2], q[3]]; }

function qrotate(q, v) {
  const p = qmul(qmul(q, [v[0], v[1], v[2], 0]), qconj(q));
  return [p[0], p[1], p[2]];
}

// deviceorientation angles (degrees, intrinsic Z-X'-Y'') to a quaternion, per the W3C spec.
function quatFromEuler(alpha, beta, gamma) {
  const d = Math.PI / 360;
  const cx = Math.cos(beta * d), sx = Math.sin(beta * d);
  const cy = Math.cos(gamma * d), sy = Math.sin(gamma * d);
  const cz = Math.cos(alpha * d), sz = Math.sin(alpha * d);
  return [
    sx * cy * cz - cx * sy * sz,
    cx * sy * cz + sx * cy * sz,
    cx * cy * sz + sx * sy * cz,
    cx * cy * cz - sx * sy * sz,
  ];
}

// Device frame -> screen frame for a page rotated by `angle` degrees (screen.orientation.angle).
function toScreenFrame(q, angle) {
  const h = (-angle * Math.PI) / 360;
  return qmul(q, [0, 0, Math.sin(h), Math.cos(h)]);
}

// 1€ filter (Casiez et al.): heavy smoothing when slow (kills tremor), light when fast (no lag).
class OneEuro {
  constructor(minCutoff, beta, dCutoff = 1) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.dCutoff = dCutoff;
    this.reset();
  }

  reset() { this.x = null; this.dx = 0; }

  static alpha(cutoff, dt) { return 1 / (1 + 1 / (2 * Math.PI * cutoff * dt)); }

  filter(x, dt) {
    if (this.x === null) { this.x = x; return x; }
    this.dx += OneEuro.alpha(this.dCutoff, dt) * ((x - this.x) / dt - this.dx);
    const cutoff = this.minCutoff + this.beta * Math.abs(this.dx);
    this.x += OneEuro.alpha(cutoff, dt) * (x - this.x);
    return this.x;
  }
}

class AirPointer {
  constructor({ minCutoff = 1.5, beta = 0.2 } = {}) {
    this.fx = new OneEuro(minCutoff, beta);
    this.fy = new OneEuro(minCutoff, beta);
    this.reset();
  }

  setSmoothing(minCutoff) {
    this.fx.minCutoff = this.fy.minCutoff = minCutoff;
  }

  reset() {
    this.prev = null;
    this.angle = [0, 0];
    this.out = [0, 0];
    this.fx.reset();
    this.fy.reset();
  }

  // Returns { dx, dy, speed }: filtered pointer motion in degrees since the last
  // reading (+x right, +y down, like the screen) and its speed in deg/s.
  update(q, timeMs) {
    const none = { dx: 0, dy: 0, speed: 0 };
    const prev = this.prev;
    this.prev = { q, t: timeMs };
    if (!prev) return none;

    const dt = (timeMs - prev.t) / 1000;
    if (dt <= 0) { this.prev = prev; return none; }
    if (dt > 0.25) { this.reset(); this.prev = { q, t: timeMs }; return none; }

    // World-frame rotation since the last reading, as a rotation vector (radians).
    let d = qmul(q, qconj(prev.q));
    if (d[3] < 0) d = d.map((c) => -c);
    const s = Math.hypot(d[0], d[1], d[2]);
    const k = s > 1e-9 ? (2 * Math.atan2(s, d[3])) / s : 2;
    const w = [d[0] * k, d[1] * k, d[2] * k];

    const yaw = w[2];
    const right = qrotate(q, [1, 0, 0]);
    const level = Math.hypot(right[0], right[1]);
    // If the left-right axis points at the sky the phone is on its side; pitch is undefined.
    const pitch = level > 0.3 ? (w[0] * right[0] + w[1] * right[1]) / level : 0;

    // Turning left is a positive (counter-clockwise) yaw; tilting up is a positive pitch.
    this.angle[0] -= yaw * RAD2DEG;
    this.angle[1] -= pitch * RAD2DEG;

    const x = this.fx.filter(this.angle[0], dt);
    const y = this.fy.filter(this.angle[1], dt);
    const dx = x - this.out[0], dy = y - this.out[1];
    this.out = [x, y];
    return { dx, dy, speed: Math.hypot(dx, dy) / dt };
  }
}

if (typeof module !== 'undefined') {
  module.exports = { AirPointer, OneEuro, qmul, qrotate, quatFromEuler, toScreenFrame };
}
