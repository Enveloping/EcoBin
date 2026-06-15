package com.chinamobile.iot.pulsar.auth;




import java.io.Serializable;



public class IoTMessage implements Serializable {
    private String data;

    private Integer superMsg;

    private String pv = "1.0";

    private Long t = System.currentTimeMillis();

    private String sign;


    public String getData() {
        return data;
    }

    public void setData(String data) {
        this.data = data;
    }

    public Integer getSuperMsg() {
        return superMsg;
    }

    public void setSuperMsg(Integer superMsg) {
        this.superMsg = superMsg;
    }

    public String getPv() {
        return pv;
    }

    public void setPv(String pv) {
        this.pv = pv;
    }

    public Long getT() {
        return t;
    }

    public void setT(Long t) {
        this.t = t;
    }

    public String getSign() {
        return sign;
    }

    public void setSign(String sign) {
        this.sign = sign;
    }
}
