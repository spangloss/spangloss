// Formats the monthly revenue table.
const { pad } = require("tinyfmt");

const rows = [
  { region: "North", revenue: "1250.50" },
  { region: "South", revenue: "980.00" },
  { region: "West", revenue: "1100.10" },
];

for (const row of rows) {
  console.log(`${row.region.padEnd(8)} ${pad(row.revenue, 10)}`);
}
