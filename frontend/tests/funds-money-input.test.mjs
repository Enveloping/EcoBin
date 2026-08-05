import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  compareMoneyCny as compareMiniMoney,
  isMoneyInputDraft as isMiniMoneyDraft,
  normalizeMoneyInput as normalizeMiniMoney,
} from '../miniprogram/miniprogram/utils/decimal.ts';
import {
  isMoneyInputDraft as isWebMoneyDraft,
  normalizeMoneyInput as normalizeWebMoney,
  parseMoneyInputCent,
} from '../web/src/utils/decimal.ts';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('miniapp money input accepts zero to two decimals without rounding', () => {
  assert.equal(normalizeMiniMoney('4'), '4.00');
  assert.equal(normalizeMiniMoney('0.4'), '0.40');
  assert.equal(normalizeMiniMoney('4.'), '4.00');
  assert.equal(normalizeMiniMoney('4.56'), '4.56');
  assert.equal(normalizeMiniMoney('4.567'), null);
  assert.equal(normalizeMiniMoney('.4'), null);
  assert.equal(normalizeMiniMoney('-4'), null);
  assert.equal(isMiniMoneyDraft('4.56'), true);
  assert.equal(isMiniMoneyDraft('4.567'), false);
});

test('miniapp compares canonical amounts against the backend balance exactly', () => {
  const amount = normalizeMiniMoney('4');
  assert.ok(amount);
  assert.equal(compareMiniMoney(amount, '4.00'), 0);
  assert.equal(compareMiniMoney(amount, '3.99'), 1);
  assert.equal(compareMiniMoney(amount, '4.01'), -1);
});

test('web recharge and signed wallet adjustment share friendly input rules', () => {
  assert.equal(normalizeWebMoney('4'), '4.00');
  assert.equal(normalizeWebMoney('0.4'), '0.40');
  assert.equal(normalizeWebMoney('4.567'), null);
  assert.equal(normalizeWebMoney('-4'), null);
  assert.equal(normalizeWebMoney('-4', true), '-4.00');
  assert.equal(normalizeWebMoney('-0.4', true), '-0.40');
  assert.equal(parseMoneyInputCent('4', true), 400n);
  assert.equal(parseMoneyInputCent('-0.4', true), -40n);
  assert.equal(isWebMoneyDraft('-', true), true);
  assert.equal(isWebMoneyDraft('-0.456', true), false);
});

test('withdrawal page keeps backend authority and never treats the WeChat page as success', () => {
  const validation = source(
    '../miniprogram/miniprogram/utils/withdrawal-validation.ts',
  );
  const page = source(
    '../miniprogram/miniprogram/pages/withdrawals/withdrawals.ts',
  );
  const markup = source(
    '../miniprogram/miniprogram/pages/withdrawals/withdrawals.wxml',
  );

  assert.match(
    validation,
    /compareMoneyCny\(amountYuan, availableBalanceYuan\) > 0/,
  );
  assert.match(validation, /BALANCE_INSUFFICIENT/);
  assert.match(page, /createIntent: null as PendingWithdrawalIntent \| null/);
  assert.match(page, /const validation = validateWithdrawalAmount\(/);
  assert.match(page, /validateWithdrawalAmount/);
  assert.match(page, /requestMerchantTransfer/);
  assert.match(page, /myWithdrawal\(withdrawalNo, false\)/);
  assert.match(page, /TERMINAL_STATUSES\.has\(detail\.status\)/);
  assert.doesNotMatch(page, /parseFloat|toFixed/);
  assert.doesNotMatch(markup, /取消提现|cancelWithdrawal/);
});

test('funds clients normalize money before building command payloads', () => {
  const fundsPage = source('../web/src/pages/funds/index.tsx');
  const usersPage = source('../web/src/pages/organization-user/index.tsx');
  const openapi = source('../../contracts/http/openapi.yaml');

  assert.match(fundsPage, /grossAmountYuan: normalizedAmount/);
  assert.match(usersPage, /deltaYuan: normalizedDelta/);
  assert.match(
    openapi,
    /\/api\/v1\/miniapp\/me\/withdrawal-configuration/,
  );
});
