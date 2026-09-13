/** Display-only estimate; created orders always use the server's fee and net amounts. */
export function rechargePreview(amountYuan: string) {
  if (!/^(0|[1-9]\d*)\.\d{2}$/.test(amountYuan)) return null;
  const gross = BigInt(amountYuan.replace('.', ''));
  if (gross <= 0n) return null;
  const fee = (gross * 6n + 999n) / 1000n;
  const yuan = (cent: bigint) => `${cent / 100n}.${(cent % 100n).toString().padStart(2, '0')}`;
  return { feeYuan: yuan(fee), netAmountYuan: yuan(gross - fee) };
}
