export function placeChartTags<Tag extends { y: number }>(tags: Tag[], height: number) {
  const gap = 30;
  const top = 16;
  const bottom = height - 16;
  if (bottom < top) return [];
  const capacity = Math.floor((bottom - top) / gap) + 1;
  const sorted = tags
    .filter((tag) => Number.isFinite(tag.y) && tag.y >= 0 && tag.y <= height)
    .sort((left, right) => left.y - right.y);
  const result: (Tag & { displayY: number; column: number })[] = [];
  for (let start = 0; start < sorted.length; start += capacity) {
    const column = sorted.slice(start, start + capacity).map((tag) => ({
      ...tag,
      displayY: Math.max(top, Math.min(bottom, tag.y)),
      column: Math.floor(start / capacity),
    }));
    for (let index = 1; index < column.length; index++) {
      column[index].displayY = Math.max(column[index].displayY, column[index - 1].displayY + gap);
    }
    if (column.length) {
      column[column.length - 1].displayY = Math.min(column[column.length - 1].displayY, bottom);
      for (let index = column.length - 2; index >= 0; index--) {
        column[index].displayY = Math.min(column[index].displayY, column[index + 1].displayY - gap);
      }
    }
    result.push(...column);
  }
  return result;
}
