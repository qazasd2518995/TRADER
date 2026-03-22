interface StatCardProps {
  label: string;
  value: string;
  valueColor?: string;
  className?: string;
}

export default function StatCard({ label, value, valueColor, className = "" }: StatCardProps) {
  return (
    <div className={`stat-block ${className}`}>
      <div
        className="heading-sm"
        style={{ fontSize: "10px", marginBottom: "2px" }}
      >
        {label}
      </div>
      <div
        className="num-md"
        style={{ color: valueColor || "var(--color-ink)" }}
      >
        {value}
      </div>
    </div>
  );
}
