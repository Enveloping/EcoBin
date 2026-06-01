package org.enveloping.ecobin;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
@MapperScan("org.enveloping.ecobin.**.mapper")
public class EcoBinApplication {

    public static void main(String[] args) {
        SpringApplication.run(EcoBinApplication.class, args);
    }

}
