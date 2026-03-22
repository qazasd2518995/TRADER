import { S, ORDER_TYPE_NAMES } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";

export default function Positions() {
  const positions = useTradingStore((s) => s.positions);
  const orders = useTradingStore((s) => s.orders);

  return (
    <div className="flex flex-col gap-6">
      {/* Open positions */}
      <div className="section stagger-1">
        <div className="flex items-center justify-between" style={{ marginBottom: "16px" }}>
          <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
            {S.NAV_POSITIONS}
          </span>
          <span className="num-sm" style={{ color: "var(--color-ink-muted)" }}>
            {positions.length} 筆
          </span>
        </div>

        {positions.length === 0 ? (
          <Empty text={S.EMPTY_POSITIONS} />
        ) : (
          <div style={{ overflow: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{S.POS_TICKET}</th>
                  <th>{S.POS_DIRECTION}</th>
                  <th>{S.POS_VOLUME}</th>
                  <th>{S.POS_ENTRY_PRICE}</th>
                  <th>{S.POS_CURRENT_PRICE}</th>
                  <th>{S.POS_SL}</th>
                  <th>{S.POS_TP}</th>
                  <th>{S.POS_PROFIT}</th>
                  <th>{S.POS_COMMENT}</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const isBuy = pos.type === 0 || pos.type === "buy";
                  return (
                    <tr key={pos.ticket}>
                      <td>{pos.ticket}</td>
                      <td style={{ color: isBuy ? "var(--color-profit)" : "var(--color-loss)" }}>
                        {isBuy ? S.POS_BUY : S.POS_SELL}
                      </td>
                      <td>{pos.volume.toFixed(2)}</td>
                      <td>{pos.price_open.toFixed(2)}</td>
                      <td>{pos.price_current.toFixed(2)}</td>
                      <td>{pos.sl.toFixed(2)}</td>
                      <td>{pos.tp.toFixed(2)}</td>
                      <td style={{ color: pos.profit >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 500 }}>
                        {pos.profit >= 0 ? "+" : ""}{pos.profit.toFixed(2)}
                      </td>
                      <td style={{ color: "var(--color-ink-muted)", fontFamily: "var(--font-sans)", fontSize: "12px" }}>
                        {pos.comment}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pending orders */}
      <div className="section stagger-2">
        <div className="flex items-center justify-between" style={{ marginBottom: "16px" }}>
          <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
            {S.ORDER_TITLE}
          </span>
          <span className="num-sm" style={{ color: "var(--color-ink-muted)" }}>
            {orders.length} 筆
          </span>
        </div>

        {orders.length === 0 ? (
          <Empty text={S.EMPTY_ORDERS} />
        ) : (
          <div style={{ overflow: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{S.POS_TICKET}</th>
                  <th>{S.ORDER_TYPE}</th>
                  <th>{S.POS_VOLUME}</th>
                  <th>{S.ORDER_PRICE}</th>
                  <th>{S.POS_SL}</th>
                  <th>{S.POS_TP}</th>
                  <th>{S.POS_COMMENT}</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.ticket}>
                    <td>{order.ticket}</td>
                    <td>{ORDER_TYPE_NAMES[order.type] ?? String(order.type)}</td>
                    <td>{order.volume.toFixed(2)}</td>
                    <td>{order.price.toFixed(2)}</td>
                    <td>{order.sl.toFixed(2)}</td>
                    <td>{order.tp.toFixed(2)}</td>
                    <td style={{ color: "var(--color-ink-muted)", fontFamily: "var(--font-sans)", fontSize: "12px" }}>
                      {order.comment}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div
      className="text-center"
      style={{
        padding: "48px 0",
        color: "var(--color-ink-ghost)",
        fontSize: "13px",
        fontStyle: "italic",
      }}
    >
      {text}
    </div>
  );
}
