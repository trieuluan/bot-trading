export interface TreeLabel {
  title: string;
  policy: string;
  params: string;
  generation: number;
  short: string;
  full: string;
}

type Source = { label?: TreeLabel | null; generation?: number } | undefined;

/** Nguồn tên duy nhất là backend (`tree_label`); id chỉ dùng làm hậu tố khi thiếu nhãn. */
export function treeLabel(source: Source, id: string): TreeLabel {
  const short = id.replace(/^hast[23]_/, '').slice(0, 4);
  if (source?.label) return source.label;
  return {
    title: `Cây #${short}`,
    policy: '',
    params: '',
    generation: source?.generation ?? 0,
    short,
    full: `Cây #${short}`,
  };
}

/** Rút gọn id băm cho chip dòng dõi. */
export const shortTreeId = (id: string) => id.replace(/^hast[23]_/, '').slice(0, 4);

/** Tên trong danh sách bỏ tiền tố chung; tooltip vẫn dùng `full`. */
export const compactTreeTitle = (title: string) => title.replace(/^Hai chiều · thuận H4 · /, '');

export function chartTreeTag(source: Source, id: string): string {
  const label = treeLabel(source, id);
  return `G${label.generation} #${label.short}`;
}
