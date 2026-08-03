// FacecamBoxPicker — the pointer-drag math that turns a mouse drag over a
// locally-loaded screenshot into a {x,y,w,h} fraction of the rendered image,
// which is what actually gets sent to the backend
// (reframe_ops.resolve_facecam_box_from_fractions consumes it downstream).
import { test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FacecamBoxPicker } from './facecamBoxPicker';

const FAKE_URL = 'blob:fake-url';

beforeEach(() => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue(FAKE_URL);
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});

afterEach(() => vi.restoreAllMocks());

function uploadScreenshot() {
  const file = new File(['x'], 'shot.png', { type: 'image/png' });
  const input = document.querySelector('input[type="file"]');
  fireEvent.change(input, { target: { files: [file] } });
}

function mockContainerRect(container) {
  // jsdom's getBoundingClientRect returns all-zero by default, which would
  // divide-by-zero the fraction math — stub a realistic rendered size.
  vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
    left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500,
  });
}

test('uploading a screenshot renders the image and no rectangle yet', () => {
  render(<FacecamBoxPicker box={null} onChange={vi.fn()} />);
  uploadScreenshot();
  expect(screen.getByRole('img')).toHaveAttribute('src', FAKE_URL);
});

test('dragging a rectangle reports a fractional box on pointer-up', () => {
  const onChange = vi.fn();
  render(<FacecamBoxPicker box={null} onChange={onChange} />);
  uploadScreenshot();
  const drawArea = screen.getByRole('img').parentElement;
  mockContainerRect(drawArea);

  fireEvent.pointerDown(drawArea, { button: 0, clientX: 600, clientY: 50, pointerId: 1 });
  fireEvent.pointerMove(drawArea, { clientX: 950, clientY: 200, pointerId: 1 });
  fireEvent.pointerUp(drawArea, { pointerId: 1 });

  expect(onChange).toHaveBeenCalledTimes(1);
  const box = onChange.mock.calls[0][0];
  expect(box.x).toBeCloseTo(0.6, 5);
  expect(box.y).toBeCloseTo(0.1, 5);
  expect(box.w).toBeCloseTo(0.35, 5);
  expect(box.h).toBeCloseTo(0.3, 5);
});

test('a tiny accidental drag (near-zero size) is not reported', () => {
  const onChange = vi.fn();
  render(<FacecamBoxPicker box={null} onChange={onChange} />);
  uploadScreenshot();
  const drawArea = screen.getByRole('img').parentElement;
  mockContainerRect(drawArea);

  fireEvent.pointerDown(drawArea, { button: 0, clientX: 500, clientY: 250, pointerId: 1 });
  fireEvent.pointerMove(drawArea, { clientX: 501, clientY: 251, pointerId: 1 });
  fireEvent.pointerUp(drawArea, { pointerId: 1 });

  expect(onChange).not.toHaveBeenCalled();
});

test('drag direction is normalized (dragging up-left still yields a positive box)', () => {
  const onChange = vi.fn();
  render(<FacecamBoxPicker box={null} onChange={onChange} />);
  uploadScreenshot();
  const drawArea = screen.getByRole('img').parentElement;
  mockContainerRect(drawArea);

  fireEvent.pointerDown(drawArea, { button: 0, clientX: 950, clientY: 200, pointerId: 1 });
  fireEvent.pointerMove(drawArea, { clientX: 600, clientY: 50, pointerId: 1 });
  fireEvent.pointerUp(drawArea, { pointerId: 1 });

  const box = onChange.mock.calls[0][0];
  expect(box.x).toBeCloseTo(0.6, 5);
  expect(box.y).toBeCloseTo(0.1, 5);
  expect(box.w).toBeCloseTo(0.35, 5);
  expect(box.h).toBeCloseTo(0.3, 5);
});

test('reset button only shows once a box exists, and clears it', () => {
  const onChange = vi.fn();
  const { rerender } = render(<FacecamBoxPicker box={null} onChange={onChange} />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();

  rerender(<FacecamBoxPicker box={{ x: 0.1, y: 0.1, w: 0.2, h: 0.2 }} onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: /reset/i }));
  expect(onChange).toHaveBeenCalledWith(null);
});
