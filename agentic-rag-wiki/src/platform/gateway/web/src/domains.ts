// 可见域枚举的单一源：入库勾选、下架展示、改可见域三处共用同一份标签表。
// 各页面另抄一份＝口径分叉（同一个 tag 在一页叫「财务」、另一页显示成裸 tag），故在此集中。
// 真实按主体归因的 ACL 与源系统权限映射属鉴权/连接器放行档，这里只是执行档演示档的域词表。
export interface DomainTag {
  tag: string;
  label: string;
}

export const DOMAINS: DomainTag[] = [
  { tag: "fin", label: "财务" },
  { tag: "hr", label: "人力" },
  { tag: "eng", label: "工程" },
];

export function domainLabel(tag: string): string {
  return DOMAINS.find((d) => d.tag === tag)?.label ?? tag;
}
