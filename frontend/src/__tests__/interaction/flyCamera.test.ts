/**
 * The fly camera's basis, checked against the orbit rig it has to agree with.
 *
 * The reference identity, straight out of `updateCamera` in both renderers:
 *
 *     eye = pivot + zoom * (cos(rx)·sin(ry), sin(rx), cos(rx)·cos(ry))
 *
 * so `forward` must point from the eye back at the pivot. These tests assert
 * that relationship directly rather than re-deriving the trigonometry, which
 * would just repeat the implementation and pass on a shared sign error.
 */
import { describe, it, expect } from 'vitest';
import { cameraBasis, flyStep, approachEye, type Vec3 } from '../../interaction/flyCamera';

/** The eye position the renderers compute, for a pivot at the origin. */
function eyeOffset(rx: number, ry: number, zoom: number): Vec3 {
    return [
        zoom * Math.cos(rx) * Math.sin(ry),
        zoom * Math.sin(rx),
        zoom * Math.cos(rx) * Math.cos(ry),
    ];
}

const close = (a: number, b: number) => expect(a).toBeCloseTo(b, 6);
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: Vec3) => Math.sqrt(dot(a, a));

const ANGLES: Array<[number, number]> = [
    [0, 0],
    [0, Math.PI / 2],
    [0.4, 1.1],
    [-0.9, -2.3],
    [1.4, 3.0],
];

describe('cameraBasis', () => {
    it('points forward from the eye toward the pivot', () => {
        for (const [rx, ry] of ANGLES) {
            const { forward } = cameraBasis(rx, ry);
            const off = eyeOffset(rx, ry, 100);
            // forward must be the exact opposite of the eye offset direction.
            close(dot(forward, [off[0] / 100, off[1] / 100, off[2] / 100]), -1);
        }
    });

    it('emits unit vectors', () => {
        for (const [rx, ry] of ANGLES) {
            const { forward, right } = cameraBasis(rx, ry);
            close(len(forward), 1);
            close(len(right), 1);
        }
    });

    it('keeps right perpendicular to forward and horizontal', () => {
        for (const [rx, ry] of ANGLES) {
            const { forward, right } = cameraBasis(rx, ry);
            close(dot(forward, right), 0);
            // Never rolls: strafing must not change altitude.
            close(right[1], 0);
        }
    });

    it('is +X to the right when looking down -Z', () => {
        // rx=0, ry=0 puts the eye at +Z looking back at the origin.
        const { forward, right } = cameraBasis(0, 0);
        close(forward[2], -1);
        close(right[0], 1);
    });
});

describe('flyStep', () => {
    const LIMIT = 10_000;

    it('moves the pivot along the view direction for W', () => {
        const moved = flyStep([0, 0, 0], 0, 0, 100, { forward: 1, right: 0, up: 0 }, LIMIT);
        // Looking down -Z, so forward travel decreases Z.
        expect(moved[2]).toBeLessThan(0);
        close(moved[0], 0);
        close(moved[1], 0);
    });

    it('reverses exactly for S', () => {
        const f = flyStep([0, 0, 0], 0.3, 1.2, 100, { forward: 1, right: 0, up: 0 }, LIMIT);
        const b = flyStep([0, 0, 0], 0.3, 1.2, 100, { forward: -1, right: 0, up: 0 }, LIMIT);
        close(f[0], -b[0]);
        close(f[1], -b[1]);
        close(f[2], -b[2]);
    });

    it('strafes without changing altitude', () => {
        const moved = flyStep([0, 5, 0], 0.7, 2.0, 100, { forward: 0, right: 1, up: 0 }, LIMIT);
        close(moved[1], 5);
    });

    it('uses WORLD up for Q/E regardless of where the camera looks', () => {
        // Steeply tilted: a camera-relative "up" would leak into X/Z here.
        const moved = flyStep([0, 0, 0], 1.4, 2.2, 100, { forward: 0, right: 0, up: 1 }, LIMIT);
        close(moved[0], 0);
        close(moved[2], 0);
        expect(moved[1]).toBeGreaterThan(0);
    });

    it('scales the step with zoom, so speed feels constant at any scale', () => {
        const near = flyStep([0, 0, 0], 0, 0, 100, { forward: 1, right: 0, up: 0 }, LIMIT);
        const far = flyStep([0, 0, 0], 0, 0, 1000, { forward: 1, right: 0, up: 0 }, LIMIT);
        expect(Math.abs(far[2])).toBeGreaterThan(Math.abs(near[2]) * 5);
    });

    it('keeps a minimum step when zoomed all the way in', () => {
        const moved = flyStep([0, 0, 0], 0, 0, 0.001, { forward: 1, right: 0, up: 0 }, LIMIT);
        expect(Math.abs(moved[2])).toBeGreaterThanOrEqual(1);
    });

    it('clamps to the limit so a held key cannot fly off to infinity', () => {
        let p: Vec3 = [0, 0, 0];
        for (let i = 0; i < 5000; i++) {
            p = flyStep(p, 0, 0, 500, { forward: 1, right: 1, up: 1 }, 200);
        }
        expect(Math.abs(p[0])).toBeLessThanOrEqual(200);
        expect(Math.abs(p[1])).toBeLessThanOrEqual(200);
        expect(Math.abs(p[2])).toBeLessThanOrEqual(200);
    });
});

describe('approachEye', () => {
    const FROM: Vec3 = [0, 0, 0];
    const TO: Vec3 = [100, 50, -20];

    it('glides a fraction of the way when smoothing is on', () => {
        const next = approachEye(FROM, TO, true, 0.1);
        close(next[0], 10);
        close(next[1], 5);
        close(next[2], -2);
    });

    it('converges on the target over repeated frames', () => {
        let p: Vec3 = FROM;
        for (let i = 0; i < 200; i++) p = approachEye(p, TO, true, 0.1);
        close(p[0], TO[0]);
        close(p[1], TO[1]);
        close(p[2], TO[2]);
    });

    /**
     * The bug this exists for. The render loop parks after one frame when
     * motion is reduced and no key is held, so a glide would strand the camera
     * a tenth of the way to the node the user just selected — and every
     * subsequent arrow press would creep another tenth and stop again. The cut
     * has to arrive within that single frame.
     */
    it('arrives in ONE frame when smoothing is off', () => {
        const next = approachEye(FROM, TO, false, 0.1);
        expect(next).toEqual(TO);
    });

    it('is a no-op once already at the target', () => {
        const next = approachEye(TO, TO, true, 0.1);
        close(next[0], TO[0]);
        close(next[1], TO[1]);
        close(next[2], TO[2]);
    });
});
